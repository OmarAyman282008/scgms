"""Shared fixtures for the SCGMS test-suite."""
import io
import shutil
import tempfile
from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AcademicYear, Semester

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def pdf(name="evidence.pdf"):
    return SimpleUploadedFile(name, PDF_BYTES, content_type="application/pdf")


def docx(name="minutes.docx"):
    from docx import Document
    buf = io.BytesIO()
    d = Document()
    d.add_paragraph("محضر اجتماع — test")
    d.save(buf)
    return SimpleUploadedFile(name, buf.getvalue(),
                              content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


class BaseCase(TestCase):
    """Creates an academic year around *today*, management and plain staff users,
    and a private temporary MEDIA_ROOT."""

    PASSWORD = "Test@12345"

    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp(prefix="scgms-test-media-")
        cls._override = override_settings(
            MEDIA_ROOT=cls._media, CELERY_TASK_ALWAYS_EAGER=True,
            STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                      "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        start = date(today.year if today.month >= 9 else today.year - 1, 9, 1)
        cls.year = AcademicYear.objects.create(name=f"{start.year}-{start.year + 1}", start_date=start,
                                               end_date=date(start.year + 1, 8, 31), is_current=True)
        Semester.objects.create(academic_year=cls.year, name_ar="الخريف", name_en="Fall", start_date=start,
                                end_date=date(start.year + 1, 1, 31))
        mk = cls.make_user
        cls.dean = mk("dean", global_role="dean")
        cls.vice = mk("vice", global_role="vice_dean")
        cls.admin = mk("sysadmin", global_role="admin", is_staff=True, is_superuser=True)
        cls.chair = mk("chair")
        cls.secretary = mk("secretary")
        cls.member = mk("member")
        cls.member2 = mk("member2")
        cls.outsider = mk("outsider")
        cls.today = today

    @classmethod
    def make_user(cls, username, global_role="staff", **extra):
        u = User.objects.create_user(username=username, password=cls.PASSWORD, global_role=global_role,
                                     name_ar=f"أ. {username}", name_en=username.title(), **extra)
        u.must_change_password = False
        u.save()
        return u

    def login(self, user):
        self.client.logout()
        self.client.force_login(user)

    def past(self, days):
        return self.today - timedelta(days=days)

    def future(self, days):
        return self.today + timedelta(days=days)
