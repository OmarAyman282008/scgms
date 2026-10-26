"""Daily reminders (run by Celery beat, or `python manage.py send_reminders` from cron).
Each reminder carries a dedupe key, so running it twice in a day sends nothing new."""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.committees.models import Meeting
from apps.governance.models import Decision, Task

from .models import Notification, SystemSettings
from .services import notify

K = Notification.Kind


def run_daily_reminders():
    s = SystemSettings.get()
    today = timezone.localdate()
    tag = today.isoformat()
    counts = {}
    mgmt = list(User.objects.filter(is_active=True, global_role__in=["dean", "vice_dean"]))

    n = 0
    for m in Meeting.objects.filter(status="scheduled", date__gte=today,
                                    date__lte=today + timedelta(days=s.meeting_reminder_days)).select_related("committee"):
        people = [x.user for x in m.committee.active_memberships()]
        n += len(notify(people, K.MEETING_UPCOMING, f"Reminder: {m.committee.name_en} meets on {m.date}",
                        f"تذكير: اجتماع {m.committee.name_ar} يوم {m.date}",
                        reverse("committees:meeting", args=[m.id]), dedupe_key=f"mup-{m.id}", dedupe_hours=72))
    counts["meetings"] = n

    n = 0
    for m in Meeting.objects.filter(date__lt=today).exclude(status="cancelled").select_related("committee", "mom"):
        if m.mom.status not in ("draft", "revision"):
            continue
        due = m.date + timedelta(days=s.mom_grace_days)
        if due - today <= timedelta(days=2):
            late = today > due
            n += len(notify([m.committee.chair, m.committee.secretary], K.MOM_DUE,
                            f"MOM {'overdue' if late else 'due'} for {m.committee.name_en} meeting of {m.date}",
                            f"محضر اجتماع {m.committee.name_ar} بتاريخ {m.date} {'متأخر' if late else 'مستحق قريبًا'}",
                            reverse("committees:meeting", args=[m.id]) + "#mom", dedupe_key=f"momdue-{m.id}-{tag}"))
    counts["moms"] = n

    n = 0
    for t in Task.objects.filter(status__in=Task.WORKING, due_date__isnull=False).select_related("committee"):
        url = reverse("governance:task", args=[t.id])
        left = (t.due_date - today).days
        if 0 <= left <= s.task_due_soon_days:
            n += len(notify(t.active_assignees(), K.TASK_DUE_SOON, f"{t.code} is due on {t.due_date}",
                            f"موعد المهمة {t.code} يوم {t.due_date}", url, dedupe_key=f"due-{t.id}-{tag}"))
        elif left < 0:
            extra = [t.committee.chair] if t.committee_id else []
            n += len(notify(t.active_assignees() + extra, K.TASK_OVERDUE, f"{t.code} is overdue by {-left} days",
                            f"المهمة {t.code} متأخرة {-left} يوم", url, dedupe_key=f"ovd-{t.id}-{tag}"))
            if -left >= s.escalate_after_overdue_days:
                n += len(notify(mgmt, K.ESCALATION, f"{t.code} overdue {-left} days — needs management attention",
                                f"المهمة {t.code} متأخرة {-left} يوم — تحتاج تدخل الإدارة", url,
                                dedupe_key=f"esc-{t.id}", dedupe_hours=24 * 7))
    counts["tasks"] = n

    n = 0
    for d in Decision.objects.filter(status__in=Decision.OPEN_STATUSES, target_date__lt=today).select_related("committee"):
        n += len(notify([d.responsible, d.committee.chair], K.DECISION_OVERDUE, f"Decision {d.code} is past its target date",
                        f"تجاوز القرار {d.code} موعده المستهدف", reverse("governance:decision", args=[d.id]),
                        dedupe_key=f"dovd-{d.id}", dedupe_hours=24 * 7))
    counts["decisions"] = n

    if s.email_notifications_enabled:
        since = timezone.now() - timedelta(days=1)
        for u in User.objects.filter(is_active=True, daily_digest_only=True, email_notifications=True).exclude(email=""):
            items = list(u.notifications.filter(created_at__gte=since, emailed=False))
            if not items:
                continue
            body = "\n".join(f"- {x.title_ar} / {x.title_en}  {settings.SITE_URL}{x.url}" for x in items)
            try:
                send_mail("[SCGMS] Daily digest", body, settings.DEFAULT_FROM_EMAIL, [u.email])
                u.notifications.filter(id__in=[x.id for x in items]).update(emailed=True)
            except Exception:
                pass
    return counts
