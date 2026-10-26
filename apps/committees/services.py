"""Meeting and MOM workflow: Draft → Submitted → Under Review → Revision / Approved → Archived."""
import calendar
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core import permissions as P
from apps.core.models import AcademicYear, Notification, SystemSettings
from apps.core.services import audit, notify

from .models import MOM, Attendance, Meeting, Membership, MOMReview

K = Notification.Kind


def next_meeting_number(committee, year):
    n = Meeting.objects.filter(committee=committee, academic_year=year).aggregate(m=Max("number"))["m"]
    return (n or 0) + 1


def committee_people(committee):
    return [m.user for m in committee.active_memberships()]


@transaction.atomic
def schedule_meeting(user, committee, *, date_, start_time=None, venue="", kind=Meeting.Kind.REGULAR,
                     agenda=(), invite=True, is_demo=False):
    P.require(P.can_manage_meetings(user, committee))
    year = AcademicYear.for_date(date_)
    if year is None:
        raise ValidationError(_("Create an academic year that covers this date first."))
    m = Meeting.objects.create(committee=committee, academic_year=year, number=next_meeting_number(committee, year),
                               kind=kind, date=date_, start_time=start_time or committee.meeting_time,
                               venue=venue or committee.venue, created_by=user, is_demo=is_demo)
    MOM.objects.create(meeting=m)
    for i, line in enumerate([a for a in agenda if a.strip()], 1):
        m.agenda_items.create(order=i, title=line.strip()[:300])
    audit(user, "meeting.schedule", f"Scheduled meeting {m.number} of {committee.name_en} on {date_}",
          f"جدولة الاجتماع رقم {m.number} لـ{committee.name_ar} بتاريخ {date_}", m, committee)
    if invite:
        send_invitations(user, m)
    return m


def send_invitations(user, meeting):
    url = reverse("committees:meeting", args=[meeting.id])
    c = meeting.committee
    t = meeting.start_time.strftime("%H:%M") if meeting.start_time else ""
    notify(committee_people(c), K.MEETING_INVITE,
           f"Meeting invitation: {c.name_en} — {meeting.date} {t}",
           f"دعوة لاجتماع {c.name_ar} — {meeting.date} {t}", url, dedupe_key=f"invite-{meeting.id}")
    meeting.invitation_sent_at = timezone.now()
    meeting.save(update_fields=["invitation_sent_at"])


def nth_weekday(year, month, weekday, n):
    days = [d for d in range(1, calendar.monthrange(year, month)[1] + 1) if date(year, month, d).weekday() == weekday]
    return date(year, month, days[-1] if n >= 5 else days[min(n, len(days)) - 1])


def regular_dates(committee, start, end):
    if not committee.has_schedule:
        return []
    out, wd = [], committee.meeting_weekday
    if committee.meeting_frequency in ("weekly", "biweekly"):
        step = 7 if committee.meeting_frequency == "weekly" else 14
        d = start + timedelta(days=(wd - start.weekday()) % 7)
        while d <= end:
            out.append(d)
            d += timedelta(days=step)
    else:
        y, mth = start.year, start.month
        while date(y, mth, 1) <= end:
            d = nth_weekday(y, mth, wd, committee.meeting_week or 1)
            if start <= d <= end:
                out.append(d)
            y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
    return out


@transaction.atomic
def generate_regular_meetings(user, committee, start, end):
    P.require(P.can_manage_meetings(user, committee))
    existing = set(committee.meetings.values_list("date", flat=True))
    made = []
    for d in regular_dates(committee, max(start, timezone.localdate()), end):
        if d not in existing:
            made.append(schedule_meeting(user, committee, date_=d, invite=False))
    if made:
        audit(user, "meeting.generate", f"Generated {len(made)} regular meetings for {committee.name_en}",
              f"إنشاء {len(made)} اجتماعات دورية لـ{committee.name_ar}", committee=committee)
    return made


@transaction.atomic
def record_attendance(user, meeting, statuses):
    """statuses: {user_id: 'present'|'absent'|'excused'}; marks the meeting as held."""
    P.require(P.can_manage_meetings(user, meeting.committee))
    roles = {m.user_id: m.role for m in meeting.committee.active_memberships()}
    for uid, st in statuses.items():
        Attendance.objects.update_or_create(meeting=meeting, user_id=uid,
                                            defaults={"status": st, "role_snapshot": roles.get(uid, "")})
    if meeting.status == Meeting.Status.SCHEDULED and meeting.date <= timezone.localdate():
        meeting.status = Meeting.Status.HELD
        meeting.save(update_fields=["status"])
    audit(user, "meeting.attendance", f"Recorded attendance for {meeting.label}",
          f"تسجيل حضور الاجتماع {meeting.label}", meeting, meeting.committee)


@transaction.atomic
def cancel_meeting(user, meeting, reason):
    P.require(P.can_manage_meetings(user, meeting.committee))
    if meeting.mom.status not in (MOM.Status.DRAFT,):
        raise ValidationError(_("A meeting with submitted minutes cannot be cancelled."))
    meeting.status = Meeting.Status.CANCELLED
    meeting.cancel_reason = reason[:300]
    meeting.save(update_fields=["status", "cancel_reason"])
    audit(user, "meeting.cancel", f"Cancelled {meeting.label}: {reason}", f"إلغاء {meeting.label}: {reason}",
          meeting, meeting.committee)


def _review(mom, user, action, to_status, comment=""):
    MOMReview.objects.create(mom=mom, actor=user, action=action, from_status=mom.status, to_status=to_status,
                             comment=comment)
    mom.status = to_status


def _mgmt_users():
    from apps.accounts.models import User
    return User.objects.filter(is_active=True, global_role__in=["dean", "vice_dean"])


@transaction.atomic
def submit_mom(user, meeting, uploaded_file, summary=""):
    from apps.documents.models import Document
    from apps.documents.services import store_file
    P.require(P.can_manage_meetings(user, meeting.committee))
    mom = meeting.mom
    if meeting.status == Meeting.Status.CANCELLED:
        raise ValidationError(_("The meeting was cancelled."))
    if meeting.date > timezone.localdate():
        raise ValidationError(_("Minutes can be uploaded after the meeting date."))
    if mom.status not in (MOM.Status.DRAFT, MOM.Status.REVISION, MOM.Status.SUBMITTED):
        raise ValidationError(_("These minutes are already under review or approved."))
    doc, _v = store_file(uploaded_file, user, title=f"MOM {meeting.label} {meeting.date}", category=Document.Category.MOM,
                         document=mom.document, meeting=meeting, allowed=["pdf", "docx", "doc"])
    mom.document = doc
    mom.discussion_summary = summary or mom.discussion_summary
    mom.submitted_at = timezone.now()
    mom.submitted_by = user
    _review(mom, user, MOMReview.Action.SUBMIT, MOM.Status.SUBMITTED)
    mom.save()
    if meeting.status == Meeting.Status.SCHEDULED:
        meeting.status = Meeting.Status.HELD
        meeting.save(update_fields=["status"])
    c = meeting.committee
    url = reverse("committees:meeting", args=[meeting.id]) + "#mom"
    chair = c.chair
    if chair is not None and chair == user:
        # the chair uploaded the minutes: the chair stage is passed at once
        _chair_pass(user, mom, "")
    else:
        reviewers = [chair] if chair is not None else list(_mgmt_users())
        notify(reviewers, K.MOM_REVIEW, f"MOM awaiting approval: {c.name_en} {meeting.date}",
               f"محضر بانتظار الاعتماد: {c.name_ar} {meeting.date}", url, dedupe_key=f"momrev-{mom.id}-chair")
    audit(user, "mom.submit", f"Submitted MOM for {meeting.label}", f"رفع محضر {meeting.label}", meeting, c)
    return mom


def _chair_pass(user, mom, comment):
    meeting, c = mom.meeting, mom.meeting.committee
    mom.chair_approved_at = timezone.now()
    if SystemSettings.get().mom_requires_management_approval:
        _review(mom, user, MOMReview.Action.CHAIR_APPROVE, MOM.Status.UNDER_REVIEW, comment)
        mom.save()
        notify(_mgmt_users(), K.MOM_REVIEW, f"MOM awaiting management approval: {c.name_en} {meeting.date}",
               f"محضر بانتظار اعتماد الإدارة: {c.name_ar} {meeting.date}",
               reverse("committees:meeting", args=[meeting.id]) + "#mom", dedupe_key=f"momrev-{mom.id}-mgmt")
    else:
        _review(mom, user, MOMReview.Action.CHAIR_APPROVE, MOM.Status.APPROVED, comment)
        _finalise(user, mom)


def _finalise(user, mom):
    mom.approved_at = timezone.now()
    mom.approved_by = user
    mom.save()
    if mom.document:
        mom.document.is_locked = True
        mom.document.save(update_fields=["is_locked"])
    m = mom.meeting
    notify([mom.submitted_by] + committee_people(m.committee), K.MOM_RESULT,
           f"MOM approved: {m.committee.name_en} {m.date} — decisions can now be registered",
           f"تم اعتماد محضر {m.committee.name_ar} {m.date} — يمكن الآن تسجيل القرارات",
           reverse("committees:meeting", args=[m.id]) + "#decisions", dedupe_key=f"momok-{mom.id}")


@transaction.atomic
def review_mom(user, meeting, approve, comment=""):
    mom, c = meeting.mom, meeting.committee
    url = reverse("committees:meeting", args=[meeting.id]) + "#mom"
    if mom.status == MOM.Status.SUBMITTED:
        P.require(P.can_chair_review_mom(user, c))
        if approve:
            _chair_pass(user, mom, comment)
        else:
            if not comment.strip():
                raise ValidationError(_("Write what needs to change."))
            _review(mom, user, MOMReview.Action.CHAIR_RETURN, MOM.Status.REVISION, comment)
            mom.save()
    elif mom.status == MOM.Status.UNDER_REVIEW:
        P.require(P.can_management_approve_mom(user))
        if approve:
            _review(mom, user, MOMReview.Action.MGMT_APPROVE, MOM.Status.APPROVED, comment)
            _finalise(user, mom)
        else:
            if not comment.strip():
                raise ValidationError(_("Write what needs to change."))
            _review(mom, user, MOMReview.Action.MGMT_RETURN, MOM.Status.REVISION, comment)
            mom.save()
    else:
        raise ValidationError(_("These minutes are not awaiting review."))
    if mom.status == MOM.Status.REVISION:
        notify([mom.submitted_by, c.secretary], K.MOM_RESULT, f"MOM returned for revision: {c.name_en} {meeting.date}",
               f"أعيد المحضر للتعديل: {c.name_ar} {meeting.date}", url, dedupe_key=f"momret-{mom.id}-{mom.reviews.count()}")
    verb_en = "approved" if approve else "returned"
    audit(user, f"mom.{verb_en}", f"MOM {meeting.label} {verb_en}: {comment}",
          f"{'اعتماد' if approve else 'إعادة'} محضر {meeting.label}: {comment}", meeting, c)
    return mom


@transaction.atomic
def archive_mom_if_complete(mom, user=None):
    """Archive approved minutes once none of their decisions is still open."""
    if mom.status != MOM.Status.APPROVED:
        return False
    if mom.meeting.decisions.filter(status__in=("registered", "assigned", "in_progress")).exists():
        return False
    MOMReview.objects.create(mom=mom, actor=user or mom.approved_by, action=MOMReview.Action.ARCHIVE,
                             from_status=mom.status, to_status=MOM.Status.ARCHIVED)
    mom.status = MOM.Status.ARCHIVED
    mom.archived_at = timezone.now()
    mom.save(update_fields=["status", "archived_at"])
    return True


@transaction.atomic
def reopen_mom(user, meeting, reason):
    P.require(P.is_exec(user))
    mom = meeting.mom
    if not mom.is_approved:
        raise ValidationError(_("Only approved minutes can be reopened."))
    if not reason.strip():
        raise ValidationError(_("A reason is required."))
    _review(mom, user, MOMReview.Action.REOPEN, MOM.Status.REVISION, reason)
    mom.save()
    if mom.document:
        mom.document.is_locked = False
        mom.document.save(update_fields=["is_locked"])
    audit(user, "mom.reopen", f"Reopened approved MOM {meeting.label}: {reason}",
          f"إعادة فتح المحضر المعتمد {meeting.label}: {reason}", meeting, meeting.committee)


@transaction.atomic
def set_membership(user, committee, member, role, note=""):
    """Change a person's role: the old membership row is closed, history is kept."""
    P.require(P.can_manage_committee(user, committee))
    today = timezone.localdate()
    current = committee.memberships.filter(user=member, end_date__isnull=True).first()
    if current and current.role == role:
        if note != current.note:
            current.note = note
            current.save(update_fields=["note"])
        return current
    if role in (Membership.Role.CHAIR, Membership.Role.SECRETARY):
        for other in committee.memberships.filter(end_date__isnull=True, role=role).exclude(user=member):
            other.end_date = today
            other.save(update_fields=["end_date"])
            Membership.objects.create(committee=committee, user=other.user, role=Membership.Role.MEMBER,
                                      note=other.note, start_date=today, order=other.order)
    order = current.order if current else committee.memberships.count()
    if current:
        current.end_date = today
        current.save(update_fields=["end_date"])
    m = Membership.objects.create(committee=committee, user=member, role=role, note=note, start_date=today, order=order)
    audit(user, "membership.set", f"{member.name_en or member.username} is now {role} of {committee.name_en}",
          f"{member.name_ar} أصبح {Membership.Role(role).label} في {committee.name_ar}", m, committee)
    return m


@transaction.atomic
def end_membership(user, membership):
    P.require(P.can_manage_committee(user, membership.committee))
    membership.end_date = timezone.localdate()
    membership.save(update_fields=["end_date"])
    audit(user, "membership.end", f"Ended membership of {membership.user.username} in {membership.committee.name_en}",
          f"إنهاء عضوية {membership.user.name_ar} في {membership.committee.name_ar}", membership, membership.committee)
