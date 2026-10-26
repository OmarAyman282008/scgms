"""The eight management reports. Each returns a Report that the view shows as HTML
and exports to Excel or PDF."""
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.utils import timezone
from django.utils.translation import get_language

from apps.committees.models import Meeting
from apps.core.models import SystemSettings
from apps.governance.models import Decision, Task

from . import kpi as KPI


def en():
    return (get_language() or "ar").startswith("en")


def T(e, a):
    return e if en() else a


def pct(v):
    return "—" if v is None else f"{v}%"


def dstr(d):
    return d.strftime("%Y-%m-%d") if d else "—"


@dataclass
class Report:
    slug: str
    title: str
    period: str
    columns: list
    rows: list
    summary: list = field(default_factory=list)
    note: str = ""


REPORTS = [
    ("monthly-performance", "Monthly Committee Performance Report", "تقرير الأداء الشهري للجان"),
    ("meeting-compliance", "Committee Meeting Compliance Report", "تقرير الالتزام بعقد الاجتماعات"),
    ("mom-submission", "MOM Submission and Approval Report", "تقرير رفع واعتماد المحاضر"),
    ("decision-implementation", "Decision Implementation Report", "تقرير تنفيذ القرارات"),
    ("outstanding-tasks", "Outstanding and Overdue Tasks Report", "تقرير المهام المفتوحة والمتأخرة"),
    ("urgent-assignments", "Urgent Executive Assignment Report", "تقرير التكليفات العاجلة من الإدارة"),
    ("decision-closure", "Decision Closure and Evidence Report", "تقرير إغلاق القرارات والشواهد"),
    ("annual-achievement", "Annual Committee Achievement Report", "تقرير الإنجاز السنوي للجان"),
]


def title_of(slug):
    for s, e, a in REPORTS:
        if s == slug:
            return T(e, a)
    return slug


def cname(c):
    return c.name_en if en() else c.name_ar


def uname(u):
    return u.display_name if u else "—"


def build(slug, f):
    today = timezone.localdate()
    period = f"{f.start} → {f.end}"
    ids = [c.id for c in f.committees]
    if slug == "monthly-performance":
        month = f.raw.get("month") or today.strftime("%Y-%m")
        y, m = map(int, month.split("-"))
        start = date(y, m, 1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        ks = KPI.compute_all(f.committees, start, end)
        cols = [T("Committee", "اللجنة"), T("Meetings held / due", "الاجتماعات المنعقدة / المستحقة"),
                T("MOM on time", "المحاضر في موعدها"), T("Decision implementation", "تنفيذ القرارات"),
                T("Task completion", "إنجاز المهام"), T("On-time", "في الموعد"), T("Overdue tasks", "مهام متأخرة"),
                T("Score", "الدرجة")]
        rows = [[cname(k.committee), f"{k.meetings_held} / {k.meetings_scheduled}", pct(k.rates['mom'].pct),
                 pct(k.rates['decision'].pct), pct(k.rates['task'].pct), pct(k.rates['ontime'].pct), k.overdue_tasks,
                 pct(k.score)] for k in ks]
        return Report(slug, title_of(slug), month, cols, rows,
                      [(T("Committees", "اللجان"), len(ks)),
                       (T("Average score", "متوسط الدرجة"),
                        pct(round(sum(k.score for k in ks if k.score is not None) /
                                  max(1, sum(1 for k in ks if k.score is not None)))))],
                      T("Rates count only items due in the month; see Settings for the formulas and weights.",
                        "تحسب النسب على البنود المستحقة خلال الشهر فقط؛ راجع الإعدادات للمعادلات والأوزان."))

    if slug == "meeting-compliance":
        cols = [T("Committee", "اللجنة"), T("Scheduled (due)", "مجدولة (مستحقة)"), T("Held", "منعقدة"),
                T("Cancelled", "ملغاة"), T("Upcoming", "قادمة"), T("Compliance", "نسبة الالتزام"),
                T("Last meeting", "آخر اجتماع")]
        rows = []
        for c in f.committees:
            ms = list(Meeting.objects.filter(committee=c, date__gte=f.start, date__lte=f.end))
            due = [m for m in ms if m.date <= today and m.status != "cancelled"]
            held = [m for m in due if m.status == "held"]
            last = max((m.date for m in held), default=None)
            rows.append([cname(c), len(due), len(held), sum(1 for m in ms if m.status == "cancelled"),
                         sum(1 for m in ms if m.date > today and m.status != "cancelled"),
                         pct(round(100 * len(held) / len(due)) if due else None), dstr(last)])
        return Report(slug, title_of(slug), period, cols, rows)

    if slug == "mom-submission":
        grace = SystemSettings.get().mom_grace_days
        cols = [T("Committee", "اللجنة"), T("No.", "رقم"), T("Meeting date", "تاريخ الاجتماع"),
                T("Submitted", "تاريخ الرفع"), T("Days after meeting", "أيام بعد الاجتماع"), T("On time", "في الموعد"),
                T("Status", "الحالة"), T("Approved", "تاريخ الاعتماد")]
        rows = []
        qs = Meeting.objects.filter(committee_id__in=ids, date__gte=f.start, date__lte=min(f.end, today)).exclude(
            status="cancelled").select_related("committee", "mom").order_by("committee_id", "date")
        n_sub = n_ok = n_app = 0
        for m in qs:
            sub = timezone.localtime(m.mom.submitted_at).date() if m.mom.submitted_at else None
            days = (sub - m.date).days if sub else (today - m.date).days
            ok = sub is not None and days <= grace
            n_sub += bool(sub)
            n_ok += ok
            n_app += m.mom.is_approved
            rows.append([cname(m.committee), m.number, dstr(m.date), dstr(sub), days if sub else f"({days})",
                         T("Yes", "نعم") if ok else T("No", "لا"), m.mom.get_status_display(),
                         dstr(timezone.localtime(m.mom.approved_at).date() if m.mom.approved_at else None)])
        total = len(rows)
        return Report(slug, title_of(slug), period, cols, rows, [
            (T("Meetings due", "اجتماعات مستحقة"), total), (T("MOMs submitted", "محاضر مرفوعة"), n_sub),
            (T("Submitted on time", "مرفوعة في الموعد"), pct(round(100 * n_ok / total) if total else None)),
            (T("Approved", "معتمدة"), pct(round(100 * n_app / n_sub) if n_sub else None))],
            T(f"On time = within {grace} days of the meeting. Days in brackets = not submitted yet.",
              f"في الموعد = خلال {grace} أيام من الاجتماع. الأيام بين قوسين = لم يُرفع بعد."))

    if slug == "decision-implementation":
        cols = [T("Decision ID", "رقم القرار"), T("Committee", "اللجنة"), T("Decision", "القرار"),
                T("Priority", "الأولوية"), T("Responsible", "المسؤول"), T("Target date", "الموعد المستهدف"),
                T("Tasks verified", "المهام المتحقق منها"), T("Status", "الحالة"), T("Closed", "تاريخ الإغلاق")]
        qs = Decision.objects.filter(committee_id__in=ids, meeting__date__gte=f.start, meeting__date__lte=f.end,
                                     is_actionable=True).select_related("committee", "responsible").prefetch_related("tasks")
        if f.status:
            qs = qs.filter(status=f.status)
        rows = []
        for d in qs.order_by("committee_id", "created_at"):
            ts = [t for t in d.tasks.all() if t.status != "cancelled"]
            status = d.get_status_display() + (T(" (overdue)", " (متأخر)") if d.is_overdue else "")
            rows.append([d.code, cname(d.committee), d.title, d.get_priority_display(), uname(d.responsible),
                         dstr(d.target_date), f"{sum(1 for t in ts if t.status == 'verified')} / {len(ts)}", status,
                         dstr(timezone.localtime(d.closure_date).date() if d.closure_date else None)])
        impl = sum(1 for d in qs if d.status == "implemented")
        return Report(slug, title_of(slug), period, cols, rows, [
            (T("Actionable decisions", "قرارات قابلة للتنفيذ"), len(rows)), (T("Implemented", "منفذة"), impl),
            (T("Open", "مفتوحة"), sum(1 for d in qs if d.is_open)),
            (T("Overdue", "متأخرة"), sum(1 for d in qs if d.is_overdue))])

    if slug == "outstanding-tasks":
        cols = [T("Task ID", "رقم المهمة"), T("Task", "المهمة"), T("Committee", "اللجنة"), T("Responsible", "المسؤول"),
                T("Priority", "الأولوية"), T("Deadline", "الموعد"), T("Days overdue", "أيام التأخير"),
                T("Status", "الحالة"), T("Progress", "الإنجاز")]
        qs = Task.objects.filter(status__in=Task.OPEN).select_related("committee").prefetch_related("assignments__user")
        qs = qs.filter(committee_id__in=ids) if f.committee else qs
        if f.priority:
            qs = qs.filter(priority=f.priority)
        rows = []
        for t in sorted(qs, key=lambda t: (-t.days_overdue, t.due_date or date.max)):
            rows.append([t.code, t.title, cname(t.committee) if t.committee else T("Management", "الإدارة"),
                         ", ".join(uname(u) for u in t.active_assignees()), t.get_priority_display(),
                         dstr(t.due_date), t.days_overdue or "", t.get_status_display(), f"{t.progress}%"])
        return Report(slug, title_of(slug), T("as of ", "حتى ") + str(today), cols, rows, [
            (T("Open tasks", "مهام مفتوحة"), len(rows)), (T("Overdue", "متأخرة"), sum(1 for t in qs if t.is_overdue)),
            (T("Awaiting verification", "بانتظار التحقق"), sum(1 for t in qs if t.status == "submitted"))])

    if slug == "urgent-assignments":
        cols = [T("Task ID", "رقم المهمة"), T("Assignment", "التكليف"), T("Assigned by", "جهة التكليف"),
                T("Committee", "اللجنة"), T("Responsible", "المسؤول"), T("Assigned", "تاريخ التكليف"),
                T("Deadline", "الموعد"), T("Status", "الحالة"), T("Verified", "تاريخ التحقق")]
        qs = Task.objects.filter(Q_source_management()).filter(assigned_at__date__gte=f.start,
                                                               assigned_at__date__lte=f.end).select_related(
            "committee", "assigned_by", "verified_by").prefetch_related("assignments__user").order_by("-assigned_at")
        rows = [[t.code, t.title, uname(t.assigned_by), cname(t.committee) if t.committee else "—",
                 ", ".join(uname(u) for u in t.active_assignees()), dstr(timezone.localtime(t.assigned_at).date()),
                 dstr(t.due_date), t.get_status_display() + (T(" (overdue)", " (متأخر)") if t.is_overdue else ""),
                 dstr(timezone.localtime(t.verified_at).date() if t.verified_at else None)] for t in qs]
        done = sum(1 for t in qs if t.status == "verified")
        return Report(slug, title_of(slug), period, cols, rows, [
            (T("Assignments", "التكليفات"), len(rows)), (T("Verified and closed", "تم التحقق والإغلاق"), done),
            (T("Overdue", "متأخرة"), sum(1 for t in qs if t.is_overdue))])

    if slug == "decision-closure":
        cols = [T("Decision ID", "رقم القرار"), T("Committee", "اللجنة"), T("Decision", "القرار"),
                T("Registered", "تاريخ التسجيل"), T("Closed", "تاريخ الإغلاق"), T("Days to close", "أيام حتى الإغلاق"),
                T("Tasks", "المهام"), T("Evidence files", "ملفات الشواهد"), T("Verified by", "تحقق بواسطة")]
        qs = Decision.objects.filter(committee_id__in=ids, status="implemented", closure_date__date__gte=f.start,
                                     closure_date__date__lte=f.end).select_related("committee").prefetch_related(
            "tasks__evidence", "tasks__verified_by")
        rows = []
        for d in qs.order_by("-closure_date"):
            ts = [t for t in d.tasks.all() if t.status != "cancelled"]
            rows.append([d.code, cname(d.committee), d.title, dstr(d.created_at.date()),
                         dstr(timezone.localtime(d.closure_date).date()), (d.closure_date.date() - d.created_at.date()).days,
                         len(ts), sum(t.evidence.count() for t in ts),
                         ", ".join(sorted({uname(t.verified_by) for t in ts if t.verified_by}))])
        days = [r[5] for r in rows]
        return Report(slug, title_of(slug), period, cols, rows, [
            (T("Decisions closed", "قرارات مغلقة"), len(rows)),
            (T("Average days to close", "متوسط أيام الإغلاق"), round(sum(days) / len(days), 1) if days else "—")])

    if slug == "annual-achievement":
        y = f.year
        start, end = (y.start_date, y.end_date) if y else (f.start, f.end)
        ks = KPI.compute_all(f.committees, start, end)
        cols = [T("Committee", "اللجنة"), T("Meetings held", "اجتماعات منعقدة"), T("MOMs approved", "محاضر معتمدة"),
                T("Decisions issued", "قرارات صادرة"), T("Implemented", "منفذة"), T("Task completion", "إنجاز المهام"),
                T("On-time", "في الموعد"), T("Avg days to close", "متوسط أيام الإغلاق"), T("Score", "الدرجة")]
        rows = [[cname(k.committee), k.meetings_held, k.moms_approved, k.decisions_total, k.decisions_implemented,
                 pct(k.rates["task"].pct), pct(k.rates["ontime"].pct), k.avg_closure_days or "—", pct(k.score)]
                for k in ks]
        return Report(slug, title_of(slug), y.name if y else period, cols, rows, [
            (T("Committees", "اللجان"), len(ks)),
            (T("Decisions implemented", "قرارات منفذة"), sum(k.decisions_implemented for k in ks))])
    raise KeyError(slug)


def Q_source_management():
    from django.db.models import Q
    return Q(source="management") | Q(is_urgent=True)
