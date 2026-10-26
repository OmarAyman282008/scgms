from django.conf import settings
from django.db import models

from apps.core import clock
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Priority(models.TextChoices):
    LOW = "low", _("Low")
    MEDIUM = "medium", _("Medium")
    HIGH = "high", _("High")
    URGENT = "urgent", _("Urgent")


PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


class Decision(models.Model):
    class Category(models.TextChoices):
        ACADEMIC = "academic", _("Academic")
        ADMINISTRATIVE = "administrative", _("Administrative")
        QUALITY = "quality", _("Quality")
        STUDENT_AFFAIRS = "student_affairs", _("Student Affairs")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        REGISTERED = "registered", _("Registered — no tasks yet")
        ASSIGNED = "assigned", _("Assigned")
        IN_PROGRESS = "in_progress", _("In progress")
        IMPLEMENTED = "implemented", _("Implemented and verified")
        NOTED = "noted", _("Noted (non-actionable)")
        CANCELLED = "cancelled", _("Cancelled")
        SUPERSEDED = "superseded", _("Superseded")

    OPEN_STATUSES = ("registered", "assigned", "in_progress")

    code = models.CharField(_("decision ID"), max_length=40, unique=True)
    committee = models.ForeignKey("committees.Committee", on_delete=models.PROTECT, related_name="decisions")
    meeting = models.ForeignKey("committees.Meeting", on_delete=models.PROTECT, related_name="decisions")
    title = models.CharField(_("decision title"), max_length=300)
    description = models.TextField(_("decision statement"), blank=True)
    category = models.CharField(_("category"), max_length=30, choices=Category.choices, default=Category.ACADEMIC)
    priority = models.CharField(_("priority"), max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    is_actionable = models.BooleanField(_("actionable"), default=True)
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="owned_decisions", verbose_name=_("responsible person"))
    target_date = models.DateField(_("target date"), null=True, blank=True)
    required_evidence = models.TextField(_("required evidence"), blank=True)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.REGISTERED)
    closure_date = models.DateTimeField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    cancel_reason = models.TextField(blank=True)
    cancel_requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                            on_delete=models.SET_NULL, related_name="+")
    cancel_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                           on_delete=models.SET_NULL, related_name="+")
    superseded_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_demo = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("decision")
        verbose_name_plural = _("decisions")

    def __str__(self):
        return f"{self.code} — {self.title}"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_overdue(self):
        return self.is_open and self.target_date and self.target_date < timezone.localdate()

    def progress(self):
        tasks = [t for t in self.tasks.all() if t.status != Task.Status.CANCELLED]
        if not tasks:
            return 100 if self.status == self.Status.IMPLEMENTED else 0
        verified = sum(1 for t in tasks if t.status == Task.Status.VERIFIED)
        return round(100 * verified / len(tasks))


class DecisionStatusHistory(models.Model):
    decision = models.ForeignKey(Decision, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["created_at", "id"]


class Task(models.Model):
    class Source(models.TextChoices):
        DECISION = "decision", _("Meeting decision")
        PLAN = "plan", _("Annual action plan")
        COMMITTEE = "committee", _("Committee responsibility")
        MANAGEMENT = "management", _("Top management assignment")

    class Status(models.TextChoices):
        NOT_STARTED = "not_started", _("Not started")
        IN_PROGRESS = "in_progress", _("In progress")
        BLOCKED = "blocked", _("Blocked")
        SUBMITTED = "submitted", _("Submitted for verification")
        RETURNED = "returned", _("Returned for more work")
        VERIFIED = "verified", _("Verified and closed")
        CANCELLED = "cancelled", _("Cancelled")

    WORKING = ("not_started", "in_progress", "blocked", "returned")
    OPEN = ("not_started", "in_progress", "blocked", "returned", "submitted")

    code = models.CharField(_("task ID"), max_length=40, unique=True)
    source = models.CharField(_("source"), max_length=20, choices=Source.choices, default=Source.COMMITTEE)
    decision = models.ForeignKey(Decision, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks")
    committee = models.ForeignKey("committees.Committee", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="tasks", verbose_name=_("committee"))
    plan_item = models.ForeignKey("committees.ActionPlanItem", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="tasks")
    title = models.CharField(_("title"), max_length=300)
    description = models.TextField(_("description"), blank=True)
    category = models.CharField(_("category"), max_length=30, choices=Decision.Category.choices,
                                default=Decision.Category.ACADEMIC)
    priority = models.CharField(_("priority"), max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    is_urgent = models.BooleanField(_("urgent management task"), default=False)
    expected_deliverables = models.TextField(_("expected deliverables / evidence"), blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                    related_name="tasks_assigned", verbose_name=_("assigning authority"))
    assigned_at = models.DateTimeField(default=clock.now)
    due_date = models.DateField(_("deadline"), null=True, blank=True)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    progress = models.PositiveSmallIntegerField(_("progress %"), default=0)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="tasks_to_review", verbose_name=_("final reviewer"))
    submitted_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("task")
        verbose_name_plural = _("tasks")

    def __str__(self):
        return f"{self.code} — {self.title}"

    @property
    def is_overdue(self):
        return self.status in self.WORKING and self.due_date and self.due_date < timezone.localdate()

    @property
    def days_overdue(self):
        return (timezone.localdate() - self.due_date).days if self.is_overdue else 0

    @property
    def display_status(self):
        return "overdue" if self.is_overdue else self.status

    def active_assignees(self):
        return [a.user for a in self.assignments.filter(active=True).select_related("user")]


class TaskAssignment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="task_assignments")
    is_primary = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(default=clock.now)
    ended_at = models.DateTimeField(null=True, blank=True)


class TaskUpdate(models.Model):
    """Time-stamped task history: every progress update, status change, review and
    administrative action appends a row here (never edited)."""

    class Kind(models.TextChoices):
        CREATED = "created", _("Created")
        PROGRESS = "progress", _("Progress update")
        OBSTACLE = "obstacle", _("Obstacle reported")
        EVIDENCE = "evidence", _("Evidence uploaded")
        SUBMITTED = "submitted", _("Submitted for verification")
        VERIFIED = "verified", _("Verified")
        RETURNED = "returned", _("Returned")
        REASSIGNED = "reassigned", _("Reassigned")
        EXTENSION = "extension", _("Deadline extension")
        ESCALATION = "escalation", _("Escalation")
        COMMENT = "comment", _("Comment")
        CANCELLED = "cancelled", _("Cancelled")
        EDITED = "edited", _("Edited")

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="updates")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    progress = models.PositiveSmallIntegerField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-created_at", "-id"]


class Evidence(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="evidence")
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(_("what this file shows"), max_length=300, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(default=clock.now)
    review_round = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["-submitted_at"]


class Verification(models.Model):
    class Outcome(models.TextChoices):
        APPROVED = "approved", _("Approved — verified and closed")
        RETURNED = "returned", _("Returned for more work")

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="verifications")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    comment = models.TextField(blank=True)
    review_round = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-created_at"]


class ExtensionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="extension_requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    current_due = models.DateField(null=True, blank=True)
    requested_due = models.DateField(_("new deadline requested"))
    reason = models.TextField(_("reason"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-created_at"]


class Escalation(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        RESOLVED = "resolved", _("Resolved")

    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.CASCADE, related_name="escalations")
    decision = models.ForeignKey(Decision, null=True, blank=True, on_delete=models.CASCADE, related_name="escalations")
    committee = models.ForeignKey("committees.Committee", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="escalations")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField(_("what is preventing completion"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-created_at"]
