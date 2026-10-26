from django.conf import settings
from django.db import models

from apps.core import clock
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _


class AcademicYear(models.Model):
    name = models.CharField(_("name"), max_length=20, unique=True, help_text="2026-2027")
    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"))
    is_current = models.BooleanField(_("current year"), default=False)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = _("academic year")
        verbose_name_plural = _("academic years")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_current:
            AcademicYear.objects.exclude(pk=self.pk).update(is_current=False)

    @classmethod
    def current(cls):
        return cls.objects.filter(is_current=True).first() or cls.objects.first()

    @classmethod
    def for_date(cls, d):
        return cls.objects.filter(start_date__lte=d, end_date__gte=d).first() or cls.current()


class Semester(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="semesters")
    name_ar = models.CharField(max_length=60)
    name_en = models.CharField(max_length=60)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["start_date"]
        verbose_name = _("semester")
        verbose_name_plural = _("semesters")

    def __str__(self):
        name = self.name_en if (get_language() or "ar").startswith("en") else self.name_ar
        return f"{name} {self.academic_year.name}"


class SystemSettings(models.Model):
    """Single row (pk=1) of configurable school policy."""

    mom_grace_days = models.PositiveSmallIntegerField(
        _("days allowed to submit a MOM after the meeting"), default=7)
    mom_requires_management_approval = models.BooleanField(
        _("MOMs need management approval after the chair"), default=True)
    email_notifications_enabled = models.BooleanField(_("send e-mail notifications"), default=False)
    meeting_reminder_days = models.PositiveSmallIntegerField(_("remind members N days before a meeting"), default=2)
    task_due_soon_days = models.PositiveSmallIntegerField(_("warn N days before a task deadline"), default=3)
    escalate_after_overdue_days = models.PositiveSmallIntegerField(
        _("flag for management after N overdue days"), default=7)
    w_meeting = models.PositiveSmallIntegerField(_("weight: meeting compliance"), default=15)
    w_mom = models.PositiveSmallIntegerField(_("weight: MOM submission compliance"), default=15)
    w_decision = models.PositiveSmallIntegerField(_("weight: decision implementation"), default=30)
    w_task = models.PositiveSmallIntegerField(_("weight: task completion"), default=20)
    w_ontime = models.PositiveSmallIntegerField(_("weight: on-time completion"), default=20)

    class Meta:
        verbose_name = _("system settings")
        verbose_name_plural = _("system settings")

    def __str__(self):
        return str(_("System settings"))

    @classmethod
    def get(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj


class AuditLog(models.Model):
    """Append-only trail of every significant action."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    committee = models.ForeignKey("committees.Committee", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="audit_logs")
    message_en = models.CharField(max_length=500)
    message_ar = models.CharField(max_length=500)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=clock.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("audit log entry")
        verbose_name_plural = _("audit log")

    @property
    def message(self):
        return self.message_en if (get_language() or "ar").startswith("en") else self.message_ar


class Comment(models.Model):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="comments")
    body = models.TextField(_("comment"))
    task = models.ForeignKey("governance.Task", null=True, blank=True, on_delete=models.CASCADE, related_name="comments")
    decision = models.ForeignKey("governance.Decision", null=True, blank=True, on_delete=models.CASCADE,
                                 related_name="comments")
    meeting = models.ForeignKey("committees.Meeting", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="comments")
    is_management = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["created_at"]


class Notification(models.Model):
    class Kind(models.TextChoices):
        MEETING_UPCOMING = "meeting_upcoming", _("Upcoming meeting")
        MEETING_INVITE = "meeting_invite", _("Meeting invitation")
        MOM_DUE = "mom_due", _("MOM submission due")
        MOM_REVIEW = "mom_review", _("MOM awaiting your approval")
        MOM_RESULT = "mom_result", _("MOM review result")
        TASK_ASSIGNED = "task_assigned", _("New task assigned")
        TASK_URGENT = "task_urgent", _("Urgent management task")
        TASK_DUE_SOON = "task_due_soon", _("Task deadline approaching")
        TASK_OVERDUE = "task_overdue", _("Task overdue")
        DECISION_OVERDUE = "decision_overdue", _("Decision overdue")
        VERIFY_REQUEST = "verify_request", _("Evidence submitted for verification")
        TASK_RETURNED = "task_returned", _("Task returned for revision")
        TASK_VERIFIED = "task_verified", _("Task verified and closed")
        DECISION_CLOSED = "decision_closed", _("Decision implemented and closed")
        COMMENT = "comment", _("Management comment")
        EXTENSION = "extension", _("Deadline extension")
        ESCALATION = "escalation", _("Escalation")

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    title_en = models.CharField(max_length=300)
    title_ar = models.CharField(max_length=300)
    url = models.CharField(max_length=300, blank=True)
    dedupe_key = models.CharField(max_length=120, blank=True, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)
    emailed = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=clock.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def title(self):
        return self.title_en if (get_language() or "ar").startswith("en") else self.title_ar
