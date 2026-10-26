import json
from collections import Counter, defaultdict
from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.committees.models import MOM, Committee, Meeting
from apps.governance.models import PRIORITY_ORDER, Decision, Escalation, ExtensionRequest, Task, TaskUpdate
from apps.reports import kpi as KPI

from . import permissions as P
from .filters import Filters
from .models import AcademicYear, AuditLog, Notification, SystemSettings
from .services import audit


def is_en():
    return (get_language() or "ar").startswith("en")


@login_required
def home(request):
    return redirect("core:overview" if P.sees_everything(request.user) else "core:me")


# ------------------------------------------------------------ executive overview
def _attention_items(user, committee_ids):
    """What requires management attention today — used by the overview and the follow-up center."""
    today = timezone.localdate()
    tasks = Task.objects.filter(Q(committee_id__in=committee_ids) | Q(committee__isnull=True)).select_related(
        "committee", "decision").prefetch_related("assignments__user")
    open_tasks = [t for t in tasks.filter(status__in=Task.OPEN)]
    items = {
        "urgent": sorted([t for t in open_tasks if t.is_urgent or t.source == "management"],
                         key=lambda t: (t.due_date or date.max)),
        "overdue_tasks": sorted([t for t in open_tasks if t.is_overdue], key=lambda t: t.due_date),
        "verification": [t for t in open_tasks if t.status == "submitted" and P.can_verify_task(user, t)],
        "overdue_decisions": list(Decision.objects.filter(committee_id__in=committee_ids,
                                                          status__in=Decision.OPEN_STATUSES, target_date__lt=today)
                                  .select_related("committee", "responsible").order_by("target_date")),
        "pending_decisions": list(Decision.objects.filter(committee_id__in=committee_ids,
                                                          status__in=Decision.OPEN_STATUSES)
                                  .select_related("committee", "responsible", "meeting").order_by("target_date")),
        "mom_approvals": [m for m in MOM.objects.filter(meeting__committee_id__in=committee_ids,
                                                        status__in=[MOM.Status.UNDER_REVIEW, MOM.Status.SUBMITTED])
                          .select_related("meeting__committee", "submitted_by")
                          if (m.status == MOM.Status.UNDER_REVIEW and P.can_management_approve_mom(user)) or
                          (m.status == MOM.Status.SUBMITTED and P.can_chair_review_mom(user, m.meeting.committee))],
        "escalations": list(Escalation.objects.filter(status=Escalation.Status.OPEN).select_related(
            "task", "committee", "raised_by")),
        "extensions": [r for r in ExtensionRequest.objects.filter(status="pending").select_related("task__committee",
                                                                                                    "requested_by")
                       if P.can_manage_task(user, r.task)],
        "cancellations": list(Decision.objects.filter(cancel_requested=True, committee_id__in=committee_ids)
                              .select_related("committee")) if P.is_exec(user) else [],
    }
    since = timezone.now() - timedelta(days=30)
    items["recently_closed_tasks"] = list(tasks.filter(status="verified", verified_at__gte=since)
                                          .select_related("verified_by").order_by("-verified_at")[:25])
    items["recently_closed_decisions"] = list(Decision.objects.filter(committee_id__in=committee_ids,
                                                                      status="implemented", closure_date__gte=since)
                                              .select_related("committee").order_by("-closure_date")[:25])
    items["overdue_count"] = len(items["overdue_tasks"]) + len(items["overdue_decisions"])
    items["esc_count"] = len(items["escalations"]) + len(items["extensions"])
    items["action_required"] = (len(items["verification"]) + len(items["mom_approvals"]) + len(items["escalations"])
                                + len(items["extensions"]) + len(items["cancellations"]))
    return items


def _blocker(task):
    """Plain answer to 'what is preventing completion?' for one task."""
    esc = next((e for e in task.escalations.all() if e.status == "open"), None)
    if esc:
        return esc.reason
    if task.status == "blocked":
        u = task.updates.filter(kind="obstacle").first()
        return u.note if u else ""
    if task.status == "returned":
        v = task.verifications.first()
        return v.comment if v else ""
    return ""


@login_required
def overview(request):
    P.require(P.sees_everything(request.user))
    f = Filters(request)
    committees = f.committees
    ids = [c.id for c in committees]
    today = timezone.localdate()
    kpis = KPI.compute_all(committees, f.start, f.end, task_filters=f.task_q() or None)
    att = _attention_items(request.user, ids)

    tasks = Task.objects.filter(committee_id__in=ids).exclude(status="cancelled").filter(**f.task_q())
    tasks_mgmt = Task.objects.filter(source="management").exclude(status="cancelled").filter(**f.task_q())
    all_tasks = list(tasks) + [t for t in tasks_mgmt if t.committee_id is None]
    decisions = Decision.objects.filter(committee_id__in=ids, meeting__date__gte=f.start, meeting__date__lte=f.end)
    if f.priority:
        decisions = decisions.filter(priority=f.priority)
    if f.person:
        decisions = decisions.filter(responsible_id=f.person)
    dec_list = list(decisions)
    meetings = Meeting.objects.filter(committee_id__in=ids, date__gte=f.start, date__lte=f.end).select_related("mom")
    m_list = list(meetings)
    held = [m for m in m_list if m.status == "held"]
    submitted = [m for m in held if m.mom.submitted_at]
    approved = [m for m in held if m.mom.is_approved]
    tile = {
        "committees": len([c for c in committees if c.is_active]),
        "meetings_scheduled": len([m for m in m_list if m.status != "cancelled"]),
        "meetings_held": len(held),
        "mom_submission_rate": round(100 * len(submitted) / len(held)) if held else None,
        "mom_approval_rate": round(100 * len(approved) / len(submitted)) if submitted else None,
        "decisions_total": len(dec_list),
        "decisions_open": sum(1 for d in dec_list if d.is_open),
        "decisions_implemented": sum(1 for d in dec_list if d.status == "implemented"),
        "tasks_done": sum(1 for t in all_tasks if t.status == "verified"),
        "tasks_ongoing": sum(1 for t in all_tasks if t.status in Task.OPEN and not t.is_overdue),
        "tasks_overdue": sum(1 for t in all_tasks if t.is_overdue),
        "urgent_open": len(att["urgent"]),
        "awaiting_mgmt": len(att["mom_approvals"]) + len(att["cancellations"]) + len(
            [t for t in att["verification"] if t.source == "management"]),
        "intervention": sum(1 for k in kpis if k.health == "critical"),
    }

    name = (lambda c: c.name_en) if is_en() else (lambda c: c.name_ar)
    curl = lambda c: reverse("committees:detail", args=[c.id])  # noqa: E731
    by_score = sorted(kpis, key=lambda k: (k.score is None, -(k.score or 0)))
    charts = {
        "performance": {"labels": [name(k.committee) for k in by_score], "values": [k.score for k in by_score],
                        "urls": [curl(k.committee) for k in by_score]},
        "mom": {"labels": [name(k.committee) for k in kpis], "values": [k.rates["mom"].pct for k in kpis],
                "urls": [curl(k.committee) for k in kpis]},
        "heat": {"y": [name(k.committee) for k in kpis], "urls": [curl(k.committee) for k in kpis],
                 "x": [(d[1] if is_en() else d[2]) for d in KPI.KPI_DEFS],
                 "z": [[k.rates[d[0]].pct for d in KPI.KPI_DEFS] for k in kpis]},
    }
    dstat = Counter()
    for d in dec_list:
        if not d.is_actionable:
            continue
        key = "overdue" if d.is_overdue else {"implemented": "implemented", "in_progress": "in_progress",
                                              "assigned": "assigned", "registered": "registered"}.get(d.status, "closed_other")
        dstat[key] += 1
    charts["decisions"] = dict(dstat)
    stack = defaultdict(lambda: Counter())
    for t in tasks:
        stack[t.committee_id]["overdue" if t.is_overdue else t.status] += 1
    charts["tasks"] = {"labels": [name(c) for c in committees], "urls": [curl(c) for c in committees],
                       "series": {s: [stack[c.id][s] for c in committees]
                                  for s in ["verified", "submitted", "in_progress", "not_started", "blocked",
                                            "returned", "overdue"]}}
    # monthly trend: tasks due vs verified per month
    months = []
    d0 = f.start.replace(day=1)
    while d0 <= min(f.end, today) and len(months) < 12:
        months.append(d0)
        d0 = (d0 + timedelta(days=32)).replace(day=1)
    due_by, ver_by = Counter(), Counter()
    for t in all_tasks:
        if t.due_date:
            due_by[t.due_date.strftime("%Y-%m")] += 1
        if t.verified_at:
            ver_by[timezone.localtime(t.verified_at).strftime("%Y-%m")] += 1
    charts["trend"] = {"labels": [m.strftime("%Y-%m") for m in months],
                       "due": [due_by[m.strftime("%Y-%m")] for m in months],
                       "verified": [ver_by[m.strftime("%Y-%m")] for m in months]}

    overdue_rows = ([("task", t, t.due_date, t.days_overdue) for t in att["overdue_tasks"]] +
                    [("decision", d, d.target_date, (today - d.target_date).days) for d in att["overdue_decisions"]])
    overdue_rows.sort(key=lambda r: -r[3])
    users = User.objects.filter(is_active=True).order_by("name_ar")
    return render(request, "core/overview.html", {
        "f": f, "kpis": kpis, "tile": tile, "att": att, "charts": charts,
        "overdue_rows": overdue_rows, "users": users, "kpi_defs": KPI.KPI_DEFS,
        "priorities": Task._meta.get_field("priority").choices, "statuses": Task.Status.choices,
    })


# ------------------------------------------------------------ my dashboard
@login_required
def me(request):
    u = request.user
    today = timezone.localdate()
    my_tasks = (Task.objects.filter(assignments__user=u, assignments__active=True, status__in=Task.OPEN)
                .select_related("committee", "decision").distinct())
    my_tasks = sorted(my_tasks, key=lambda t: (not t.is_urgent, t.due_date or date.max))
    reviewable = [t for t in Task.objects.filter(status="submitted").select_related("committee")
                  if P.can_verify_task(u, t)]
    cids = list(u.memberships.filter(end_date__isnull=True).values_list("committee_id", flat=True))
    mom_reviews = [m for m in MOM.objects.filter(status__in=["submitted", "under_review"]).select_related(
        "meeting__committee") if (m.status == "submitted" and P.can_chair_review_mom(u, m.meeting.committee)) or
        (m.status == "under_review" and P.can_management_approve_mom(u))]
    manage_ids = [c for c in cids if P.can_manage_meetings(u, Committee(id=c))]
    grace = SystemSettings.get().mom_grace_days
    moms_to_upload = [m for m in Meeting.objects.filter(committee_id__in=manage_ids, date__lte=today)
                      .exclude(status="cancelled").select_related("mom", "committee")
                      if m.mom.status in ("draft", "revision")]
    upcoming = (Meeting.objects.filter(committee_id__in=cids, date__gte=today, date__lte=today + timedelta(days=21))
                .exclude(status="cancelled").select_related("committee").order_by("date"))
    roles = u.memberships.filter(end_date__isnull=True).select_related("committee")
    my_decisions = Decision.objects.filter(responsible=u, status__in=Decision.OPEN_STATUSES).select_related("committee")
    notes = u.notifications.filter(is_read=False)[:8]
    return render(request, "core/me.html", {
        "my_tasks": my_tasks, "reviewable": reviewable, "mom_reviews": mom_reviews,
        "moms_to_upload": moms_to_upload, "upcoming": upcoming, "roles": roles, "notes": notes,
        "my_decisions": my_decisions, "grace": grace, "today": today,
    })


# ------------------------------------------------------------ follow-up center
@login_required
def followup(request):
    P.require(P.is_exec(request.user))
    f = Filters(request)
    att = _attention_items(request.user, [c.id for c in f.committees])
    for t in att["urgent"] + att["overdue_tasks"] + att["verification"]:
        t.blocker = _blocker(t)
    return render(request, "core/followup.html", {"att": att, "f": f, "section": request.GET.get("s", "action")})


# ------------------------------------------------------------ calendar
@login_required
def calendar_view(request):
    f = Filters(request)
    return render(request, "core/calendar.html", {"f": f})


@login_required
def calendar_events(request):
    ids = P.committee_ids_for(request.user)
    qs = Meeting.objects.filter(committee_id__in=ids).select_related("committee", "mom")
    if request.GET.get("committee"):
        qs = qs.filter(committee_id=request.GET["committee"])
    if request.GET.get("start"):
        qs = qs.filter(date__gte=request.GET["start"][:10])
    if request.GET.get("end"):
        qs = qs.filter(date__lte=request.GET["end"][:10])
    colors = {"approved": "#1d7a3a", "archived": "#1d7a3a", "under_review": "#2a6fdb", "submitted": "#2a6fdb",
              "revision": "#b7791f", "draft": "#6b7a90"}
    grace = SystemSettings.get().mom_grace_days
    today = timezone.localdate()
    ev = []
    for m in qs:
        if m.status == "cancelled":
            color = "#adb5bd"
        elif m.date <= today and m.mom.status == "draft" and (today - m.date).days > grace:
            color = "#c0392b"
        else:
            color = colors.get(m.mom.status, "#1f3b6e") if m.date <= today else "#1f3b6e"
        ev.append({"title": m.committee.name, "start": f"{m.date}T{m.start_time}" if m.start_time else str(m.date),
                   "url": reverse("committees:meeting", args=[m.id]), "color": color,
                   "allDay": m.start_time is None})
    return JsonResponse(ev, safe=False)


# ------------------------------------------------------------ notifications
@login_required
def notifications(request):
    qs = request.user.notifications.all()
    if request.GET.get("unread"):
        qs = qs.filter(is_read=False)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "core/notifications.html", {"page": page})


@login_required
def notification_open(request, pk):
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    n.is_read = True
    n.save(update_fields=["is_read"])
    return redirect(n.url or "core:notifications")


@login_required
def notifications_read_all(request):
    if request.method == "POST":
        request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect("core:notifications")


@login_required
def notification_badge(request):
    n = request.user.notifications.filter(is_read=False).count()
    return HttpResponse(f'<span class="badge rounded-pill bg-danger">{n}</span>' if n else "")


# ------------------------------------------------------------ administration
class SettingsForm(forms.ModelForm):
    class Meta:
        model = SystemSettings
        fields = "__all__"


@login_required
def admin_home(request):
    P.require(P.sees_everything(request.user))
    return render(request, "core/admin_home.html", {"years": AcademicYear.objects.prefetch_related("semesters")})


@login_required
def settings_view(request):
    P.require(P.is_admin(request.user) or P.is_exec(request.user))
    obj = SystemSettings.get()
    form = SettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request.user, "settings", "System settings changed", "تعديل إعدادات النظام", obj, request=request)
        messages.success(request, _("Saved."))
        return redirect("core:settings")
    return render(request, "core/settings.html", {"form": form, "kpi_defs": KPI.KPI_DEFS})


@login_required
def audit_log(request):
    P.require(P.sees_everything(request.user))
    qs = AuditLog.objects.select_related("actor", "committee")
    if request.GET.get("committee"):
        qs = qs.filter(committee_id=request.GET["committee"])
    if request.GET.get("q"):
        qs = qs.filter(Q(message_en__icontains=request.GET["q"]) | Q(message_ar__icontains=request.GET["q"]))
    if not request.GET.get("logins"):
        qs = qs.exclude(action="login")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "core/audit.html", {"page": page, "committees": Committee.objects.all()})
