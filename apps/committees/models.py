from django.conf import settings
from django.db import models

from apps.core import clock
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _


def _is_en():
    return (get_language() or "ar").startswith("en")


class Committee(models.Model):
    class Kind(models.TextChoices):
        COMMITTEE = "committee", _("Committee")
        COUNCIL = "council", _("Council / board")

    class Frequency(models.TextChoices):
        WEEKLY = "weekly", _("Weekly")
        BIWEEKLY = "biweekly", _("Every two weeks")
        MONTHLY = "monthly", _("Monthly")
        NONE = "none", _("As needed")

    class Report(models.TextChoices):
        WEEKLY = "weekly", _("Weekly report")
        MONTHLY = "monthly", _("Monthly report")
        SEMESTER = "semester", _("Semester report")
        AFTER_MIDTERM = "after_midterm", _("Report after midterm results")
        NONE = "none", _("Not specified")

    code = models.SlugField(_("code"), max_length=30, unique=True)
    name_ar = models.CharField(_("name (Arabic)"), max_length=200)
    name_en = models.CharField(_("name (English)"), max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.COMMITTEE)
    description = models.TextField(_("description"), blank=True)
    objectives = models.TextField(_("objectives"), blank=True)
    formed_on = models.DateField(_("formed on"), null=True, blank=True)
    formation_reference = models.CharField(_("formation reference"), max_length=200, blank=True)
    report_frequency = models.CharField(_("reporting"), max_length=20, choices=Report.choices, default=Report.NONE)
    meeting_frequency = models.CharField(_("meeting frequency"), max_length=20, choices=Frequency.choices,
                                         default=Frequency.MONTHLY)
    meeting_week = models.PositiveSmallIntegerField(_("week of month"), null=True, blank=True,
                                                    help_text=_("1–4, or 5 for the last week"))
    meeting_weekday = models.PositiveSmallIntegerField(_("meeting day"), null=True, blank=True,
                                                       help_text=_("0 = Monday … 6 = Sunday"))
    meeting_time = models.TimeField(_("meeting time"), null=True, blank=True)
    venue = models.CharField(_("venue"), max_length=150, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["id"]
        verbose_name = _("committee")
        verbose_name_plural = _("committees")

    def __str__(self):
        return self.name

    @property
    def name(self):
        return self.name_en if _is_en() else self.name_ar

    @property
    def other_name(self):
        return self.name_ar if _is_en() else self.name_en

    def active_memberships(self):
        return self.memberships.filter(end_date__isnull=True).select_related("user")

    def member_with_role(self, role):
        m = self.memberships.filter(end_date__isnull=True, role=role).select_related("user").first()
        return m.user if m else None

    @property
    def chair(self):
        return self.member_with_role(Membership.Role.CHAIR)

    @property
    def secretary(self):
        return self.member_with_role(Membership.Role.SECRETARY)

    @property
    def has_schedule(self):
        return self.meeting_frequency != self.Frequency.NONE and self.meeting_weekday is not None


class CommitteeResponsibility(models.Model):
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name="responsibilities")
    text_ar = models.TextField()
    text_en = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    @property
    def text(self):
        return (self.text_en or self.text_ar) if _is_en() else self.text_ar


class Membership(models.Model):
    """A user's role in a committee. Ending a membership keeps the history row."""

    class Role(models.TextChoices):
        CHAIR = "chair", _("Chair")
        SECRETARY = "secretary", _("Secretary")
        MEMBER = "member", _("Member")

    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="memberships")
    role = models.CharField(_("role"), max_length=20, choices=Role.choices, default=Role.MEMBER)
    note = models.CharField(_("assignment"), max_length=200, blank=True)
    start_date = models.DateField(_("from"), default=clock.today)
    end_date = models.DateField(_("to"), null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["end_date", "order", "id"]
        verbose_name = _("membership")
        verbose_name_plural = _("memberships")

    @property
    def is_active(self):
        return self.end_date is None


class ActionPlanItem(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", _("Planned")
        TASKED = "tasked", _("Converted to task")
        DONE = "done", _("Done")
        DROPPED = "dropped", _("Dropped")

    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name="plan_items")
    academic_year = models.ForeignKey("core.AcademicYear", on_delete=models.PROTECT, related_name="plan_items")
    title = models.CharField(_("activity"), max_length=300)
    description = models.TextField(_("details"), blank=True)
    target_date = models.DateField(_("target date"), null=True, blank=True)
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", verbose_name=_("responsible"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    is_demo = models.BooleanField(default=False)

    class Meta:
        ordering = ["target_date", "id"]


class Meeting(models.Model):
    class Kind(models.TextChoices):
        REGULAR = "regular", _("Regular monthly meeting")
        EXCEPTIONAL = "exceptional", _("Exceptional meeting")
        URGENT = "urgent", _("Urgent meeting")

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("Scheduled")
        HELD = "held", _("Held")
        CANCELLED = "cancelled", _("Cancelled")

    committee = models.ForeignKey(Committee, on_delete=models.PROTECT, related_name="meetings")
    academic_year = models.ForeignKey("core.AcademicYear", on_delete=models.PROTECT, related_name="meetings")
    number = models.PositiveSmallIntegerField(_("meeting number"))
    kind = models.CharField(_("type"), max_length=20, choices=Kind.choices, default=Kind.REGULAR)
    date = models.DateField(_("date"))
    start_time = models.TimeField(_("time"), null=True, blank=True)
    end_time = models.TimeField(_("end time"), null=True, blank=True)
    venue = models.CharField(_("venue"), max_length=150, blank=True)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    cancel_reason = models.CharField(max_length=300, blank=True)
    invitation_sent_at = models.DateTimeField(null=True, blank=True)
    is_demo = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-date", "-id"]
        unique_together = [("committee", "academic_year", "number")]
        verbose_name = _("meeting")
        verbose_name_plural = _("meetings")

    def __str__(self):
        return f"{self.committee.name} — {self.number}/{self.academic_year.name} ({self.date})"

    @property
    def label(self):
        return f"{self.committee.code.upper()}-M{self.number:02d}"


class AgendaItem(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="agenda_items")
    order = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(_("agenda item"), max_length=300)
    presenter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    follow_up_decision = models.ForeignKey("governance.Decision", null=True, blank=True, on_delete=models.SET_NULL,
                                           related_name="+")

    class Meta:
        ordering = ["order", "id"]


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", _("Present")
        ABSENT = "absent", _("Absent")
        EXCUSED = "excused", _("Excused")

    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="attendance")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRESENT)
    role_snapshot = models.CharField(max_length=20, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("meeting", "user")]


class MOM(models.Model):
    """Minutes of meeting and their approval state."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted — awaiting chair")
        UNDER_REVIEW = "under_review", _("Under management review")
        REVISION = "revision", _("Revision required")
        APPROVED = "approved", _("Approved")
        ARCHIVED = "archived", _("Archived")

    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name="mom")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    document = models.ForeignKey("documents.Document", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    discussion_summary = models.TextField(_("discussion summary"), blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    chair_approved_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    archived_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_approved(self):
        return self.status in (self.Status.APPROVED, self.Status.ARCHIVED)


class MOMReview(models.Model):
    class Action(models.TextChoices):
        SUBMIT = "submit", _("Submitted")
        CHAIR_APPROVE = "chair_approve", _("Approved by chair")
        CHAIR_RETURN = "chair_return", _("Returned by chair")
        MGMT_APPROVE = "mgmt_approve", _("Approved by management")
        MGMT_RETURN = "mgmt_return", _("Returned by management")
        ARCHIVE = "archive", _("Archived")
        REOPEN = "reopen", _("Reopened")

    mom = models.ForeignKey(MOM, on_delete=models.CASCADE, related_name="reviews")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=20, choices=Action.choices)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["created_at", "id"]
