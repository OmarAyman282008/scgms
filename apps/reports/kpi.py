"""Committee performance KPIs.

Every rate is   numerator / denominator   over a reporting period [start, end],
counting only items that were DUE by the cut-off date (the earlier of `end` and today).
Rates never grow with volume, so a committee cannot raise its score by creating more
meetings, decisions or tasks — only by completing and verifying what it owes.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from apps.committees.models import MOM, Meeting
from apps.core.models import SystemSettings
from apps.governance.models import Decision, Task

KPI_DEFS = [
    # key, English, Arabic, formula (en), formula (ar)
    ("meeting", "Meeting compliance", "الالتزام بعقد الاجتماعات",
     "Meetings held ÷ meetings scheduled up to the cut-off date (cancelled excluded)",
     "الاجتماعات المنعقدة ÷ الاجتماعات المجدولة حتى تاريخ القياس (دون الملغاة)"),
    ("mom", "MOM submission compliance", "الالتزام برفع المحاضر",
     "MOMs submitted within the grace period ÷ held meetings whose MOM is due",
     "المحاضر المرفوعة خلال المهلة ÷ الاجتماعات المنعقدة المستحق محضرها"),
    ("decision", "Decision implementation", "تنفيذ القرارات",
     "Actionable decisions implemented and verified ÷ actionable decisions due (target date passed, or closed)",
     "القرارات المنفذة والمتحقق منها ÷ القرارات القابلة للتنفيذ المستحقة"),
    ("task", "Task completion", "إنجاز المهام",
     "Tasks verified ÷ tasks due (deadline passed, or verified) — cancelled excluded",
     "المهام المتحقق منها ÷ المهام المستحقة (دون الملغاة)"),
    ("ontime", "On-time completion", "الإنجاز في الموعد",
     "Tasks verified on or before their deadline ÷ tasks due",
     "المهام المتحقق منها في موعدها ÷ المهام المستحقة"),
]


@dataclass
class Rate:
    num: int = 0
    den: int = 0

    @property
    def pct(self):
        return round(100 * self.num / self.den) if self.den else None


@dataclass
class CommitteeKPI:
    committee: object
    rates: dict = field(default_factory=dict)
    overdue_tasks: int = 0
    overdue_decisions: int = 0
    open_tasks: int = 0
    pending_verification: int = 0
    decisions_total: int = 0
    decisions_open: int = 0
    decisions_implemented: int = 0
    meetings_scheduled: int = 0
    meetings_held: int = 0
    moms_approved: int = 0
    avg_closure_days: float = None
    score: int = None

    @property
    def health(self):
        if self.overdue_tasks or self.overdue_decisions:
            return "critical"
        if self.score is not None and self.score < 60:
            return "critical"
        if self.score is None or self.score < 80:
            return "warning"
        return "good"


def weights():
    s = SystemSettings.get()
    return {"meeting": s.w_meeting, "mom": s.w_mom, "decision": s.w_decision, "task": s.w_task, "ontime": s.w_ontime}


def compute(committee, start, end, today=None, task_filters=None):
    today = today or timezone.localdate()
    cut = min(end, today)
    grace = SystemSettings.get().mom_grace_days
    k = CommitteeKPI(committee=committee)

    meetings = Meeting.objects.filter(committee=committee, date__gte=start, date__lte=end).exclude(
        status=Meeting.Status.CANCELLED).select_related("mom")
    due_m = [m for m in meetings if m.date <= cut]
    held = [m for m in due_m if m.status == Meeting.Status.HELD]
    k.meetings_scheduled, k.meetings_held = len(due_m), len(held)
    k.rates["meeting"] = Rate(len(held), len(due_m))

    mom_due = [m for m in held if m.date + timedelta(days=grace) <= today or m.mom.submitted_at]
    on_time = [m for m in mom_due if m.mom.submitted_at and
               timezone.localtime(m.mom.submitted_at).date() <= m.date + timedelta(days=grace)]
    k.rates["mom"] = Rate(len(on_time), len(mom_due))
    k.moms_approved = sum(1 for m in held if m.mom.status in (MOM.Status.APPROVED, MOM.Status.ARCHIVED))

    decisions = Decision.objects.filter(committee=committee, meeting__date__gte=start, meeting__date__lte=end,
                                        is_actionable=True).exclude(status__in=["cancelled", "superseded"])
    k.decisions_total = decisions.count()
    implemented = [d for d in decisions if d.status == Decision.Status.IMPLEMENTED]
    due_d = [d for d in decisions if d.status == "implemented" or (d.target_date and d.target_date <= cut)]
    k.decisions_implemented = len(implemented)
    k.decisions_open = sum(1 for d in decisions if d.is_open)
    k.overdue_decisions = sum(1 for d in decisions if d.is_overdue)
    k.rates["decision"] = Rate(sum(1 for d in due_d if d.status == "implemented"), len(due_d))
    durations = [(d.closure_date.date() - d.created_at.date()).days for d in implemented if d.closure_date]
    k.avg_closure_days = round(sum(durations) / len(durations), 1) if durations else None

    tasks = Task.objects.filter(committee=committee).exclude(status=Task.Status.CANCELLED)
    if task_filters:
        tasks = tasks.filter(**task_filters)
    tasks = [t for t in tasks if (t.due_date and start <= t.due_date <= end) or
             (t.verified_at and start <= timezone.localtime(t.verified_at).date() <= end)]
    due_t = [t for t in tasks if t.status == "verified" or (t.due_date and t.due_date <= cut)]
    verified = [t for t in due_t if t.status == "verified"]
    k.rates["task"] = Rate(len(verified), len(due_t))
    k.rates["ontime"] = Rate(sum(1 for t in verified if not t.due_date or
                                 timezone.localtime(t.verified_at).date() <= t.due_date), len(due_t))
    all_open = Task.objects.filter(committee=committee, status__in=Task.OPEN)
    k.open_tasks = all_open.count()
    k.overdue_tasks = sum(1 for t in all_open if t.is_overdue)
    k.pending_verification = all_open.filter(status=Task.Status.SUBMITTED).count()

    w = weights()
    parts = [(w[key], r.pct) for key, r in k.rates.items() if r.pct is not None and w[key] > 0]
    total_w = sum(p[0] for p in parts)
    k.score = round(sum(wt * pct for wt, pct in parts) / total_w) if total_w else None
    return k


def compute_all(committees, start, end, **kw):
    return [compute(c, start, end, **kw) for c in committees]
