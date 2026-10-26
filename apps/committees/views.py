from datetime import date

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.core import permissions as P
from apps.core.models import AcademicYear, AuditLog, SystemSettings
from apps.core.services import audit
from apps.governance.models import Decision, Task
from apps.reports import kpi as KPI

from . import services as S
from .models import (MOM, ActionPlanItem, AgendaItem, Committee, CommitteeResponsibility, Meeting, Membership)


def _err(request, e):
    messages.error(request, " ".join(e.messages) if hasattr(e, "messages") else str(e))


def _visible_committees(user):
    return Committee.objects.filter(id__in=P.committee_ids_for(user))


# ------------------------------------------------------------------ committees
@login_required
def committee_list(request):
    qs = _visible_committees(request.user).filter(is_active=True)
    year = AcademicYear.current()
    start, end = (year.start_date, year.end_date) if year else (date(2000, 1, 1), date(2100, 1, 1))
    kpis = KPI.compute_all(list(qs), start, end)
    inactive = _visible_committees(request.user).filter(is_active=False)
    return render(request, "committees/list.html", {"kpis": kpis, "inactive": inactive, "year": year})


class CommitteeForm(forms.ModelForm):
    class Meta:
        model = Committee
        fields = ["code", "name_ar", "name_en", "kind", "description", "objectives", "formed_on",
                  "formation_reference", "report_frequency", "meeting_frequency", "meeting_week", "meeting_weekday",
                  "meeting_time", "venue", "is_active"]
        widgets = {"formed_on": forms.DateInput(attrs={"type": "date"}),
                   "meeting_time": forms.TimeInput(attrs={"type": "time"}),
                   "description": forms.Textarea(attrs={"rows": 3}), "objectives": forms.Textarea(attrs={"rows": 3}),
                   "meeting_weekday": forms.Select(choices=[("", "—"), (6, gettext_lazy("Sunday")), (0, gettext_lazy("Monday")),
                                                            (1, gettext_lazy("Tuesday")), (2, gettext_lazy("Wednesday")),
                                                            (3, gettext_lazy("Thursday")), (4, gettext_lazy("Friday")), (5, gettext_lazy("Saturday"))]),
                   "meeting_week": forms.Select(choices=[("", "—"), (1, gettext_lazy("1st week")), (2, gettext_lazy("2nd week")),
                                                         (3, gettext_lazy("3rd week")), (4, gettext_lazy("4th week")), (5, gettext_lazy("Last week"))])}


@login_required
def committee_form(request, pk=None):
    obj = get_object_or_404(Committee, pk=pk) if pk else None
    P.require(P.can_manage_committee(request.user, obj))
    form = CommitteeForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        audit(request.user, "committee.save", f"Saved committee {c.name_en}", f"حفظ بيانات {c.name_ar}", c, c, request)
        messages.success(request, _("Saved."))
        return redirect("committees:detail", pk=c.pk)
    return render(request, "committees/form.html", {"form": form, "obj": obj})


@login_required
def committee_detail(request, pk):
    c = get_object_or_404(Committee, pk=pk)
    P.require(P.can_view_committee(request.user, c))
    tab = request.GET.get("tab", "overview")
    year = AcademicYear.current()
    start, end = (year.start_date, year.end_date) if year else (date(2000, 1, 1), date(2100, 1, 1))
    k = KPI.compute(c, start, end)
    today = timezone.localdate()
    ctx = {"c": c, "tab": tab, "k": k, "year": year, "today": today,
           "can_manage": P.can_manage_committee(request.user, c),
           "can_meet": P.can_manage_meetings(request.user, c),
           "can_task": P.can_create_committee_task(request.user, c),
           "my_roles": P.roles_in(request.user, c), "kpi_defs": KPI.KPI_DEFS}
    grace = SystemSettings.get().mom_grace_days
    meetings = c.meetings.select_related("mom", "academic_year").order_by("-date")
    ctx["moms_due"] = [m for m in meetings if m.date <= today and m.status != "cancelled"
                       and m.mom.status in ("draft", "revision")]
    ctx["open_decisions"] = c.decisions.filter(status__in=Decision.OPEN_STATUSES).select_related("responsible")
    ctx["overdue_tasks"] = [t for t in c.tasks.filter(status__in=Task.WORKING) if t.is_overdue]
    ctx["next_meeting"] = c.meetings.filter(date__gte=today, status="scheduled").order_by("date").first()
    if tab == "members":
        ctx["memberships"] = c.memberships.select_related("user").order_by("end_date", "order")
        ctx["users"] = User.objects.filter(is_active=True).order_by("name_ar")
        ctx["roles"] = Membership.Role.choices
    elif tab == "meetings":
        ctx["meetings"] = meetings
        ctx["grace"] = grace
    elif tab == "decisions":
        ctx["decisions"] = c.decisions.select_related("meeting", "responsible").prefetch_related("tasks")
    elif tab == "tasks":
        ctx["tasks"] = c.tasks.select_related("decision").prefetch_related("assignments__user").order_by("status", "due_date")
    elif tab == "plan":
        ctx["plan"] = c.plan_items.filter(academic_year=year).select_related("responsible") if year else []
        ctx["users"] = [m.user for m in c.active_memberships()]
    elif tab == "documents":
        ctx["documents"] = c.documents.select_related("meeting", "task", "decision").prefetch_related("versions")
        if not P.sees_everything(request.user):
            ctx["documents"] = ctx["documents"].filter(is_confidential=False)
    elif tab == "history":
        ctx["logs"] = AuditLog.objects.filter(committee=c).select_related("actor")[:200]
        ctx["past_members"] = c.memberships.filter(end_date__isnull=False).select_related("user")
    return render(request, "committees/detail.html", ctx)


@login_required
@require_POST
def member_set(request, pk):
    c = get_object_or_404(Committee, pk=pk)
    user = get_object_or_404(User, pk=request.POST.get("user"))
    try:
        S.set_membership(request.user, c, user, request.POST.get("role", "member"), request.POST.get("note", ""))
        messages.success(request, _("Membership updated."))
    except ValidationError as e:
        _err(request, e)
    return redirect(f"/committees/{c.pk}/?tab=members")


@login_required
@require_POST
def member_end(request, pk, mid):
    m = get_object_or_404(Membership, pk=mid, committee_id=pk)
    S.end_membership(request.user, m)
    messages.success(request, _("Membership ended; the record is kept in the history."))
    return redirect(f"/committees/{pk}/?tab=members")


@login_required
@require_POST
def responsibility_save(request, pk):
    c = get_object_or_404(Committee, pk=pk)
    P.require(P.can_manage_committee(request.user, c))
    if request.POST.get("delete"):
        CommitteeResponsibility.objects.filter(pk=request.POST["delete"], committee=c).delete()
    elif request.POST.get("text_ar", "").strip():
        CommitteeResponsibility.objects.create(committee=c, text_ar=request.POST["text_ar"].strip(),
                                               text_en=request.POST.get("text_en", "").strip(),
                                               order=c.responsibilities.count() + 1)
    audit(request.user, "committee.responsibility", f"Updated responsibilities of {c.name_en}",
          f"تحديث مهام {c.name_ar}", c, c, request)
    return redirect(f"/committees/{c.pk}/?tab=overview")


@login_required
@require_POST
def plan_save(request, pk):
    c = get_object_or_404(Committee, pk=pk)
    P.require(P.can_create_committee_task(request.user, c))
    year = AcademicYear.current()
    if request.POST.get("status_id"):
        it = get_object_or_404(ActionPlanItem, pk=request.POST["status_id"], committee=c)
        it.status = request.POST.get("status", it.status)
        it.save(update_fields=["status"])
    elif request.POST.get("title", "").strip():
        ActionPlanItem.objects.create(committee=c, academic_year=year, title=request.POST["title"].strip(),
                                      description=request.POST.get("description", ""),
                                      target_date=request.POST.get("target_date") or None,
                                      responsible_id=request.POST.get("responsible") or None)
    audit(request.user, "plan.save", f"Updated action plan of {c.name_en}", f"تحديث خطة عمل {c.name_ar}", c, c, request)
    return redirect(f"/committees/{c.pk}/?tab=plan")


@login_required
@require_POST
def generate_meetings(request, pk):
    c = get_object_or_404(Committee, pk=pk)
    try:
        end = date.fromisoformat(request.POST.get("end")) if request.POST.get("end") else AcademicYear.current().end_date
        made = S.generate_regular_meetings(request.user, c, timezone.localdate(), end)
        messages.success(request, _("%(n)s meetings added to the calendar.") % {"n": len(made)})
    except (ValidationError, ValueError) as e:
        _err(request, e)
    return redirect(f"/committees/{c.pk}/?tab=meetings")


# ------------------------------------------------------------------ meetings
@login_required
def meeting_list(request):
    ids = P.committee_ids_for(request.user)
    qs = Meeting.objects.filter(committee_id__in=ids).select_related("committee", "mom", "academic_year")
    g = request.GET
    if g.get("committee"):
        qs = qs.filter(committee_id=g["committee"])
    if g.get("status"):
        qs = qs.filter(status=g["status"])
    if g.get("mom"):
        qs = qs.filter(mom__status=g["mom"])
    when = g.get("when", "")
    today = timezone.localdate()
    if when == "upcoming":
        qs = qs.filter(date__gte=today).order_by("date")
    elif when == "past":
        qs = qs.filter(date__lt=today)
    page = Paginator(qs, 40).get_page(g.get("page"))
    return render(request, "committees/meetings.html", {
        "page": page, "committees": Committee.objects.filter(id__in=ids), "g": g, "today": today,
        "statuses": Meeting.Status.choices, "mom_statuses": MOM.Status.choices,
        "can_create": any(P.can_manage_meetings(request.user, c) for c in Committee.objects.filter(id__in=ids))})


class MeetingForm(forms.Form):
    committee = forms.ModelChoiceField(queryset=Committee.objects.none(), label=gettext_lazy("Committee"))
    kind = forms.ChoiceField(choices=Meeting.Kind.choices, label=gettext_lazy("Type"))
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label=gettext_lazy("Date"))
    start_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}), label=gettext_lazy("Time"))
    venue = forms.CharField(required=False, label=gettext_lazy("Venue"))
    agenda = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}),
                             label=gettext_lazy("Agenda (one item per line)"))
    invite = forms.BooleanField(required=False, initial=True, label=gettext_lazy("Send invitations to all members"))


@login_required
def meeting_new(request):
    allowed = [c for c in Committee.objects.filter(is_active=True) if P.can_manage_meetings(request.user, c)]
    P.require(allowed)
    form = MeetingForm(request.POST or None, initial={"committee": request.GET.get("committee"), "invite": True})
    form.fields["committee"].queryset = Committee.objects.filter(id__in=[c.id for c in allowed])
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            m = S.schedule_meeting(request.user, d["committee"], date_=d["date"], start_time=d["start_time"],
                                   venue=d["venue"], kind=d["kind"], agenda=d["agenda"].splitlines(),
                                   invite=d["invite"])
            messages.success(request, _("Meeting scheduled."))
            return redirect("committees:meeting", pk=m.pk)
        except ValidationError as e:
            _err(request, e)
    return render(request, "committees/meeting_form.html", {"form": form})


@login_required
def meeting_detail(request, pk):
    m = get_object_or_404(Meeting.objects.select_related("committee", "mom", "academic_year"), pk=pk)
    c = m.committee
    P.require(P.can_view_committee(request.user, c))
    mom = m.mom
    today = timezone.localdate()
    members = list(c.active_memberships())
    att = {a.user_id: a for a in m.attendance.all()}
    for mb in members:
        mb.att = att.get(mb.user_id)
    prev_pending = Decision.objects.filter(committee=c, status__in=Decision.OPEN_STATUSES,
                                           meeting__date__lt=m.date).select_related("responsible", "meeting")
    decisions = m.decisions.select_related("responsible").prefetch_related("tasks__assignments__user")
    u = request.user
    return render(request, "committees/meeting.html", {
        "m": m, "c": c, "mom": mom, "members": members, "prev_pending": prev_pending, "decisions": decisions,
        "today": today, "reviews": mom.reviews.select_related("actor"),
        "versions": mom.document.versions.select_related("uploaded_by") if mom.document else [],
        "can_meet": P.can_manage_meetings(u, c),
        "can_chair_review": mom.status == "submitted" and P.can_chair_review_mom(u, c),
        "can_mgmt_review": mom.status == "under_review" and P.can_management_approve_mom(u),
        "can_register": P.can_register_decisions(u, m), "can_convert": P.can_convert_decision(u, Decision(committee=c)),
        "is_exec": P.is_exec(u), "comments": m.comments.select_related("author"),
        "users": [mb.user for mb in members], "categories": Decision.Category.choices,
        "priorities": Decision._meta.get_field("priority").choices,
        "grace": SystemSettings.get().mom_grace_days,
    })


@login_required
def meeting_edit(request, pk):
    m = get_object_or_404(Meeting, pk=pk)
    P.require(P.can_manage_meetings(request.user, m.committee))
    if request.method == "POST":
        m.date = request.POST.get("date") or m.date
        m.start_time = request.POST.get("start_time") or None
        m.venue = request.POST.get("venue", "")
        m.kind = request.POST.get("kind", m.kind)
        m.save()
        audit(request.user, "meeting.edit", f"Edited {m.label}", f"تعديل الاجتماع {m.label}", m, m.committee, request)
        messages.success(request, _("Saved."))
    return redirect("committees:meeting", pk=pk)


@login_required
@require_POST
def meeting_action(request, pk):
    m = get_object_or_404(Meeting, pk=pk)
    a = request.POST.get("action")
    anchor = ""
    try:
        if a == "cancel":
            S.cancel_meeting(request.user, m, request.POST.get("reason", ""))
        elif a == "invite":
            P.require(P.can_manage_meetings(request.user, m.committee))
            S.send_invitations(request.user, m)
            messages.success(request, _("Invitations sent."))
        elif a == "attendance":
            st = {int(k.split("_")[1]): v for k, v in request.POST.items() if k.startswith("att_")}
            S.record_attendance(request.user, m, st)
            messages.success(request, _("Attendance saved."))
            anchor = "#attendance"
        elif a == "agenda_add":
            P.require(P.can_manage_meetings(request.user, m.committee))
            title = request.POST.get("title", "").strip()
            dec = request.POST.get("decision")
            if dec:
                d = get_object_or_404(Decision, pk=dec, committee=m.committee)
                AgendaItem.objects.create(meeting=m, order=m.agenda_items.count() + 1,
                                          title=f"{_('Follow-up')}: {d.code} — {d.title}"[:300], follow_up_decision=d)
            elif title:
                AgendaItem.objects.create(meeting=m, order=m.agenda_items.count() + 1, title=title[:300])
            anchor = "#agenda"
        elif a == "agenda_del":
            P.require(P.can_manage_meetings(request.user, m.committee))
            m.agenda_items.filter(pk=request.POST.get("item")).delete()
            anchor = "#agenda"
        elif a == "mom_upload":
            S.submit_mom(request.user, m, request.FILES.get("file"), request.POST.get("summary", ""))
            messages.success(request, _("Minutes uploaded and sent for review."))
            anchor = "#mom"
        elif a in ("mom_approve", "mom_return"):
            S.review_mom(request.user, m, a == "mom_approve", request.POST.get("comment", ""))
            messages.success(request, _("Review saved."))
            anchor = "#mom"
        elif a == "mom_reopen":
            S.reopen_mom(request.user, m, request.POST.get("reason", ""))
            anchor = "#mom"
        elif a == "comment":
            from apps.governance.services import add_comment
            if request.POST.get("body", "").strip():
                add_comment(request.user, request.POST["body"].strip(), meeting=m)
            anchor = "#comments"
    except ValidationError as e:
        _err(request, e)
    return redirect(f"/committees/meetings/{m.pk}/{anchor}")


@login_required
def mom_template(request, pk):
    from apps.documents.momtemplate import render as render_mom
    m = get_object_or_404(Meeting, pk=pk)
    P.require(P.can_view_committee(request.user, m.committee))
    buf = render_mom(m)
    if m.mom.status == MOM.Status.DRAFT:
        audit(request.user, "mom.template", f"Downloaded MOM template for {m.label}", f"تنزيل نموذج محضر {m.label}",
              m, m.committee, request)
    name = f"MOM_{m.committee.code}_{m.number:02d}_{m.date}.docx"
    return FileResponse(buf, as_attachment=True, filename=name,
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@login_required
def mom_list(request):
    u = request.user
    ids = P.committee_ids_for(u)
    qs = MOM.objects.filter(meeting__committee_id__in=ids).select_related("meeting__committee", "submitted_by")
    mine = [x for x in qs.filter(status__in=["submitted", "under_review"])
            if (x.status == "submitted" and P.can_chair_review_mom(u, x.meeting.committee)) or
            (x.status == "under_review" and P.can_management_approve_mom(u))]
    st = request.GET.get("status", "")
    allq = qs.exclude(status="draft") if not st else qs.filter(status=st)
    today = timezone.localdate()
    grace = SystemSettings.get().mom_grace_days
    missing = [x for x in qs.filter(status__in=["draft", "revision"], meeting__date__lt=today)
               .exclude(meeting__status="cancelled")]
    for x in missing:
        x.late_days = (today - x.meeting.date).days - grace
    page = Paginator(allq.order_by("-meeting__date"), 40).get_page(request.GET.get("page"))
    return render(request, "committees/moms.html", {"mine": mine, "page": page, "missing": missing, "st": st,
                                                    "statuses": MOM.Status.choices})
