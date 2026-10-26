import hashlib
import os
import uuid

from django.conf import settings
from django.db import models

from apps.core import clock
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def private_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower()[:10]
    d = timezone.now()
    return f"docs/{d:%Y/%m}/{uuid.uuid4().hex}{ext}"


def template_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"templates/{uuid.uuid4().hex}{ext}"


class Document(models.Model):
    """A logical document, linked to the governance record it belongs to.
    Its file content lives in DocumentVersion rows (one per upload)."""

    class Category(models.TextChoices):
        FORMATION = "formation", _("Committee formation & membership")
        ACTION_PLAN = "action_plan", _("Annual action plan")
        AGENDA = "agenda", _("Agenda")
        MOM = "mom", _("Minutes of meeting")
        DECISION = "decision", _("Approved decision")
        EVIDENCE = "evidence", _("Implementation evidence")
        REPORT = "report", _("Periodic report")
        CORRESPONDENCE = "correspondence", _("Correspondence")
        ANNUAL_REPORT = "annual_report", _("Annual performance report")
        OTHER = "other", _("Other")

    title = models.CharField(_("title"), max_length=300)
    category = models.CharField(_("type"), max_length=30, choices=Category.choices, default=Category.OTHER)
    committee = models.ForeignKey("committees.Committee", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="documents")
    academic_year = models.ForeignKey("core.AcademicYear", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="documents")
    meeting = models.ForeignKey("committees.Meeting", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="documents")
    decision = models.ForeignKey("governance.Decision", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="documents")
    task = models.ForeignKey("governance.Task", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="documents")
    is_confidential = models.BooleanField(_("confidential (management only)"), default=False)
    is_locked = models.BooleanField(default=False, help_text="Approved records cannot get new versions")
    is_demo = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("document")
        verbose_name_plural = _("documents")

    def __str__(self):
        return self.title

    @property
    def current(self):
        return self.versions.order_by("-version_no").first()


class DocumentVersion(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    version_no = models.PositiveSmallIntegerField(default=1)
    file = models.FileField(upload_to=private_upload_to, max_length=300)
    original_name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["-version_no"]
        unique_together = [("document", "version_no")]

    @property
    def extension(self):
        return os.path.splitext(self.original_name)[1].lower().lstrip(".")

    def compute_hash(self):
        h = hashlib.sha256()
        self.file.open("rb")
        for chunk in self.file.chunks():
            h.update(chunk)
        self.file.seek(0)
        return h.hexdigest()


class DocumentTemplate(models.Model):
    """Institution-approved templates (MOM). Uploading a new one keeps the old versions."""

    class Kind(models.TextChoices):
        MOM = "mom", _("Minutes of meeting (MOM)")

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MOM)
    name = models.CharField(_("name"), max_length=200)
    file = models.FileField(upload_to=template_upload_to)
    version_no = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(default=clock.now)

    class Meta:
        ordering = ["kind", "-version_no"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            DocumentTemplate.objects.filter(kind=self.kind).exclude(pk=self.pk).update(is_active=False)

    @classmethod
    def active(cls, kind="mom"):
        return cls.objects.filter(kind=kind, is_active=True).first()
