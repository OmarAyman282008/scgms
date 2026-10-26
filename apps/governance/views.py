from datetime import date

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.committees.models import ActionPlanItem, Committee, Meeting
from apps.core import permissions as P

from . import services as S
from .models import PRIORITY_ORDER, Decision, ExtensionRequest, Priority, Task


def _err(request, e):
    messages.error(request, " ".join(e.messages) if hasattr(e, "messages") else str(e))


def _d(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


# ------------------------------------------------------------------ decisions
@login_required
def decision_list(request):
    ids = P.committee_ids_for(request.user)
    qs = Decision.objects.filter(committee_id__in=ids).select_related("committee", "meeting", "responsible") \
        .prefetch_related("tasks")
    g = request.GET
    for key in ("committee", "status", "priority", "category"):
        if g.get(key):
            qs = qs.filter(**{f"{key}_id" if key == "committee" else key: g[key]})
    if g.get("person"):
        qs = qs.filter(responsible_id=g["person"])
    if g.get("q"):
        qs = qs.filter(Q(title__icontains=g["q"]) | Q(code__icontains=g["q"]) | Q(description__icontains=g["q"]))
    if g.get("overdue"):
        qs = qs.filter(status__in=Decision.OPEN_STATUSES, target_date__lt=timezone.localdate())
    if g.get("open"):
        qs = qs.filter(status__in=Decision.OPEN_STATUSES)
    page = Paginator(qs, 40).get_page(g.get("page"))
    return render(request, "governance/decisions.html", {
        "page": page, "g": g, "committees": Committee.objects.filter(id__in=ids),
        "statuses": Decision.Status.choices, "priorities": Priority.choices, "categories": Decision.Category.choices,
        "users": User.objects.filter(is_active=True)})


class DecisionForm(forms.Form):
    title = forms.CharField(max_length=300, label=gettext_lazy("Decision title"))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label=gettext_lazy("Decision statement"))
    category = forms.ChoiceField(choices=Decision.Category.choices, label=gettext_lazy("Category"))
    priority = forms.ChoiceField(choices=Priority.choices, initial="medium", label=gettext_lazy("Priority"))
    is_actionable = forms.BooleanField(required=False, initial=True, label=gettext_lazy("Actionable (needs implementation)"))
    responsible = forms.ModelChoiceField(queryset=User.objects.none(), required=False, label=gettext_lazy("Responsible person"))
    target_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label=gettext_lazy("Target date"))
    required_evidence = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                        label=gettext_lazy("Required evidence"))


@login_required
def decision_new(request, meeting_id):
    m = get_object_or_404(Meeting, pk=meeting_id)
    P.require(P.can_register_decisions(request.user, m))
    form = DecisionForm(request.POST or None)
    form.fields["responsible"].queryset = User.objects.filter(id__in=[x.user_id for x in m.committee.active_memberships()])
    if request.method == "POST" and form.is_valid():
        try:
            d = S.register_decision(request.user, m, **form.cleaned_data)
            messages.success(request, _("Decision %(code)s registered.") % {"code": d.code})
            if request.POST.get("next") == "another":
                return redirect("governance:decision_new", meeting_id=m.id)
            return redirect(f"/committees/meetings/{m.id}/#decisions")
        except ValidationError as e:
            _err(request, e)
    return render(request, "governance/decision_form.html", {"form": form, "m": m})


@login_required
def decision_detail(request, pk):
    d = get_object_or_404(Decision.objects.select_related("committee", "meeting__mom", "responsible"), pk=pk)
    P.require(P.can_view_committee(request.user, d.committee))
    tasks = d.tasks.prefetch_related("assignments__user", "evidence__document").select_related("verified_by")
    evidence = [e for t in tasks for e in t.evidence.all()]
    return render(request, "governance/decision.html", {
        "d": d, "tasks": tasks, "evidence": evidence, "history": d.history.select_related("actor"),
        "comments": d.comments.select_related("author"), "progress": d.progress(),
        "can_convert": d.is_open and P.can_convert_decision(request.user, d),
        "can_edit": P.can_manage_meetings(request.user, d.committee) and d.is_open,
        "can_request_cancel": d.is_open and not d.cancel_requested and P.can_manage_meetings(request.user, d.committee),
        "can_decide_cancel": d.cancel_requested and P.can_cancel_decision(request.user, d),
        "others": Decision.objects.filter(committee=d.committee).exclude(pk=d.pk).order_by("-created_at")[:50],
        "users": [m.user for m in d.committee.active_memberships()], "priorities": Priority.choices,
        "categories": Decision.Category.choices})


@login_required
@require_POST
def decision_action(request, pk):
    d = get_object_or_404(Decision, pk=pk)
    a = request.POST.get("action")
    try:
        if a == "edit":
            S.edit_decision(request.user, d, title=request.POST.get("title", d.title)[:300],
                            description=request.POST.get("description", ""),
                            priority=request.POST.get("priority", d.priority),
                            category=request.POST.get("category", d.category),
                            responsible_id=request.POST.get("responsible") or None,
                            target_date=_d(request.POST.get("target_date")),
                            required_evidence=request.POST.get("required_evidence", ""))
        elif a == "cancel_request":
            sup = Decision.objects.filter(pk=request.POST.get("superseded_by")).first()
            S.request_cancellation(request.user, d, request.POST.get("reason", ""), sup)
            messages.success(request, _("Cancellation request sent to management."))
        elif a in ("cancel_approve", "cancel_reject"):
            S.decide_cancellation(request.user, d, a == "cancel_approve", request.POST.get("note", ""))
        elif a == "comment" and request.POST.get("body", "").strip():
            S.add_comment(request.user, request.POST["body"].strip(), decision=d)
    except ValidationError as e:
        _err(request, e)
    return redirect("governance:decision", pk=d.pk)


@login_required
def decision_convert(request, pk):
    """Stage 2: review the information carried over from the decision, then assign."""
    d = get_object_or_404(Decision.objects.select_related("committee", "meeting__mom", "responsible"), pk=pk)
    P.require(P.can_convert_decision(request.user, d))
    members = [m.user for m in d.committee.active_memberships()]
    n = max(1, min(8, int(request.GET.get("n", "1") or 1)))
    if request.method == "POST":
        specs = []
        count = int(request.POST.get("count", "1"))
        valid_ids = {u.id for u in members} | set(User.objects.filter(global_role__in=["dean", "vice_dean"]).values_list("id", flat=True))
        for i in range(count):
            title = request.POST.get(f"title_{i}", "").strip()
            if not title:
                continue
            ids = [int(x) for x in request.POST.getlist(f"assignees_{i}") if x.isdigit() and int(x) in valid_ids]
            specs.append({"title": title, "description": request.POST.get(f"description_{i}", ""),
                          "assignees": list(User.objects.filter(id__in=ids)),
                          "due_date": _d(request.POST.get(f"due_{i}")),
                          "priority": request.POST.get(f"priority_{i}", d.priority),
                          "expected_deliverables": request.POST.get(f"deliverables_{i}", ""),
                          "reviewer": User.objects.filter(pk=request.POST.get(f"reviewer_{i}") or 0).first()})
        try:
            tasks = S.convert_decision(request.user, d, specs)
            messages.success(request, _("%(n)s task(s) created and assigned.") % {"n": len(tasks)})
            return redirect("governance:decision", pk=d.pk)
        except ValidationError as e:
            _err(request, e)
    return render(request, "governance/decision_convert.html", {
        "d": d, "rows": range(n), "n": n, "members": members, "priorities": Priority.choices,
        "reviewers": [d.committee.chair] if d.committee.chair else []})


# ------------------------------------------------------------------ tasks
@login_required
def task_list(request):
    u = request.user
    g = request.GET
    scope = g.get("scope", "mine" if not P.sees_everything(u) else "all")
    ids = P.committee_ids_for(u)
    if scope == "mine":
        qs = Task.objects.filter(assignments__user=u, assignments__active=True)
    elif scope == "review":
        qs = Task.objects.filter(status="submitted")
    else:
        qs = Task.objects.filter(Q(committee_id__in=ids) | (Q(committee__isnull=True) if P.sees_everything(u)
                                                             else Q(assignments__user=u)))
    qs = qs.select_related("committee", "decision").prefetch_related("assignments__user").distinct()
    if g.get("committee"):
        qs = qs.filter(committee_id=g["committee"])
    if g.get("priority"):
        qs = qs.filter(priority=g["priority"])
    if g.get("source"):
        qs = qs.filter(source=g["source"])
    if g.get("person"):
        qs = qs.filter(assignments__user_id=g["person"], assignments__active=True)
    if g.get("q"):
        qs = qs.filter(Q(title__icontains=g["q"]) | Q(code__icontains=g["q"]))
    st = g.get("status", "open" if scope != "review" else "")
    if st == "open":
        qs = qs.filter(status__in=Task.OPEN)
    elif st == "overdue":
        qs = qs.filter(status__in=Task.WORKING, due_date__lt=timezone.localdate())
    elif st and st != "all":
        qs = qs.filter(status=st)
    rows = list(qs)
    if scope == "review":
        rows = [t for t in rows if P.can_verify_task(u, t)]
    rows.sort(key=lambda t: (t.status in ("verified", "cancelled"), not t.is_urgent, PRIORITY_ORDER.get(t.priority, 9),
                             t.due_date or date.max))
    page = Paginator(rows, 40).get_page(g.get("page"))
    return render(request, "governance/tasks.html", {
        "page": page, "g": g, "scope": scope, "st": st, "committees": Committee.objects.filter(id__in=ids),
        "statuses": Task.Status.choices, "priorities": Priority.choices, "sources": Task.Source.choices,
        "users": User.objects.filter(is_active=True)})


class TaskForm(forms.Form):
    committee = forms.ModelChoiceField(queryset=Committee.objects.none(), required=False, label=gettext_lazy("Committee"))
    title = forms.CharField(max_length=300, label=gettext_lazy("Title"))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label=gettext_lazy("Description"))
    category = forms.ChoiceField(choices=Decision.Category.choices, label=gettext_lazy("Category"))
    priority = forms.ChoiceField(choices=Priority.choices, initial="medium", label=gettext_lazy("Priority"))
    assignees = forms.ModelMultipleChoiceField(queryset=User.objects.none(), label=gettext_lazy("Responsible member(s)"),
                                               widget=forms.SelectMultiple(attrs={"size": 8}))
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label=gettext_lazy("Deadline"))
    expected_deliverables = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                            label=gettext_lazy("Expected deliverables / evidence"))
    reviewer = forms.ModelChoiceField(queryset=User.objects.none(), required=False, label=gettext_lazy("Final reviewer"),
                                      help_text=gettext_lazy("Leave empty: the committee chair (or management for urgent tasks) verifies."))
    source_note = forms.CharField(required=False, label=gettext_lazy("Source / reference"),
                                  help_text=gettext_lazy("e.g. Dean's directive, university council decision"))


@login_required
def task_new(request):
    u = request.user
    urgent = request.GET.get("urgent") == "1" or request.POST.get("urgent") == "1"
    if urgent:
        P.require(P.can_assign_urgent(u))
    plan = ActionPlanItem.objects.filter(pk=request.GET.get("plan") or request.POST.get("plan") or 0).first()
    allowed = [c for c in Committee.objects.filter(is_active=True) if P.can_create_committee_task(u, c)]
    P.require(allowed or urgent)
    initial = {"priority": "urgent" if urgent else "medium"}
    if plan:
        initial.update(committee=plan.committee_id, title=plan.title, description=plan.description,
                       due_date=plan.target_date, assignees=[plan.responsible_id] if plan.responsible_id else [])
    elif request.GET.get("committee"):
        initial["committee"] = request.GET["committee"]
    form = TaskForm(request.POST or None, initial=initial)
    form.fields["committee"].queryset = Committee.objects.filter(id__in=[c.id for c in allowed]) if not urgent \
        else Committee.objects.filter(is_active=True)
    form.fields["committee"].required = not urgent
    people = User.objects.filter(is_active=True).order_by("name_ar")
    form.fields["assignees"].queryset = people
    form.fields["reviewer"].queryset = people
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        desc = d["description"] + (f"\n\n{_('Source')}: {d['source_note']}" if d["source_note"] else "")
        try:
            if not urgent:
                allowed_ids = {x.user_id for x in d["committee"].active_memberships()}
                if any(a.id not in allowed_ids for a in d["assignees"]) and not P.is_exec(u):
                    raise ValidationError(_("Assign committee members only."))
            t = S.create_task(u, title=d["title"], description=desc, category=d["category"], priority=d["priority"],
                              assignees=list(d["assignees"]), due_date=d["due_date"], committee=d["committee"],
                              expected_deliverables=d["expected_deliverables"], reviewer=d["reviewer"],
                              source=Task.Source.MANAGEMENT if urgent else (Task.Source.PLAN if plan else Task.Source.COMMITTEE),
                              plan_item=plan, is_urgent=urgent)
            messages.success(request, _("Task %(code)s assigned.") % {"code": t.code})
            return redirect("governance:task", pk=t.pk)
        except ValidationError as e:
            _err(request, e)
    return render(request, "governance/task_form.html", {"form": form, "urgent": urgent, "plan": plan})


@login_required
def task_detail(request, pk):
    t = get_object_or_404(Task.objects.select_related("committee", "decision__meeting", "assigned_by", "reviewer",
                                                      "verified_by"), pk=pk)
    u = request.user
    P.require(P.can_view_task(u, t))
    rnd = t.verifications.filter(outcome="returned").count() + 1
    pending_ext = t.extension_requests.filter(status="pending").first()
    members = ([m.user for m in t.committee.active_memberships()] if t.committee_id
               else list(User.objects.filter(is_active=True)))
    return render(request, "governance/task.html", {
        "t": t, "assignees": t.assignments.filter(active=True).select_related("user"),
        "past_assignees": t.assignments.filter(active=False).select_related("user"),
        "updates": t.updates.select_related("actor"), "evidence": t.evidence.select_related("document", "submitted_by"),
        "verifications": t.verifications.select_related("reviewer"), "comments": t.comments.select_related("author"),
        "escalations": t.escalations.select_related("raised_by", "resolved_by"),
        "extensions": t.extension_requests.select_related("requested_by", "decided_by"),
        "pending_ext": pending_ext, "round": rnd,
        "round_evidence": t.evidence.filter(review_round=rnd).count(),
        "can_update": P.can_update_task(u, t), "can_verify": P.can_verify_task(u, t),
        "can_manage": P.can_manage_task(u, t), "is_exec": P.is_exec(u), "members": members,
        "priorities": Priority.choices, "reviewers": User.objects.filter(is_active=True)})


@login_required
@require_POST
def task_action(request, pk):
    t = get_object_or_404(Task, pk=pk)
    u = request.user
    a = request.POST.get("action")
    p = request.POST
    try:
        if a == "progress":
            S.update_progress(u, t, progress=p.get("progress", t.progress), note=p.get("note", ""),
                              blocked=bool(p.get("blocked")))
            messages.success(request, _("Progress saved."))
        elif a == "evidence":
            files = request.FILES.getlist("files")
            if not files:
                raise ValidationError(_("Choose a file first."))
            for f in files:
                S.add_evidence(u, t, f, p.get("description", ""))
            messages.success(request, _("Evidence uploaded."))
        elif a == "submit":
            S.submit_for_verification(u, t, p.get("note", ""))
            messages.success(request, _("Submitted for verification."))
        elif a in ("verify", "return"):
            S.verify_task(u, t, a == "verify", p.get("comment", ""))
            messages.success(request, _("Review saved."))
        elif a == "extension":
            S.request_extension(u, t, _d(p.get("new_due")), p.get("reason", ""))
            messages.success(request, _("Extension request sent."))
        elif a in ("ext_approve", "ext_reject"):
            r = get_object_or_404(ExtensionRequest, pk=p.get("req"), task=t)
            S.decide_extension(u, r, a == "ext_approve", p.get("note", ""))
        elif a == "escalate":
            S.escalate(u, t, p.get("reason", ""))
            messages.success(request, _("Escalated to management."))
        elif a == "resolve":
            esc = get_object_or_404(t.escalations, pk=p.get("esc"))
            S.resolve_escalation(u, esc, p.get("resolution", ""))
        elif a == "reassign":
            S.reassign(u, t, list(User.objects.filter(id__in=p.getlist("assignees"))), p.get("note", ""))
        elif a == "edit":
            S.edit_task(u, t, title=p.get("title", t.title)[:300], due_date=_d(p.get("due_date")),
                        priority=p.get("priority", t.priority),
                        reviewer=User.objects.filter(pk=p.get("reviewer") or 0).first())
        elif a == "cancel":
            S.cancel_task(u, t, p.get("reason", ""))
        elif a == "comment" and p.get("body", "").strip():
            S.add_comment(u, p["body"].strip(), task=t)
    except ValidationError as e:
        _err(request, e)
    nxt = p.get("next")
    if nxt and nxt.startswith("/"):
        return redirect(nxt)
    return redirect("governance:task", pk=t.pk)
