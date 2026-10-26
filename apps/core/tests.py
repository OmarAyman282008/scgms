"""Role-based access, data isolation, upload security, KPI formulas and page smoke tests."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.committees import services as CS
from apps.committees.models import Committee
from apps.core import permissions as P
from apps.core.testutils import BaseCase, docx, pdf
from apps.documents.models import Document
from apps.documents.services import store_file, validate_upload
from apps.governance import services as S
from apps.reports import kpi as KPI


class Fixture(BaseCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.com = Committee.objects.create(code="AAA", name_ar="لجنة أ", name_en="Committee A")
        cls.other = Committee.objects.create(code="BBB", name_ar="لجنة ب", name_en="Committee B")
        CS.set_membership(cls.admin, cls.com, cls.chair, "chair")
        CS.set_membership(cls.admin, cls.com, cls.secretary, "secretary")
        CS.set_membership(cls.admin, cls.com, cls.member, "member")
        CS.set_membership(cls.admin, cls.other, cls.outsider, "chair")
        # the same person can hold different roles in different committees
        CS.set_membership(cls.admin, cls.other, cls.member, "secretary")


class RoleAccess(Fixture):
    def test_login_required_everywhere(self):
        for name in ("core:overview", "core:me", "core:followup", "governance:tasks", "documents:archive",
                     "reports:index", "committees:list"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/accounts/login/", r["Location"])

    def test_executive_pages_need_management_role(self):
        self.login(self.member)
        for name in ("core:overview", "core:followup", "core:settings", "accounts:users"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.login(self.vice)
        for name in ("core:overview", "core:followup", "core:settings"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_only_sysadmin_manages_users(self):
        self.login(self.dean)
        self.assertEqual(self.client.get(reverse("accounts:user_new")).status_code, 403)
        self.login(self.admin)
        r = self.client.post(reverse("accounts:user_new"), {"username": "newbie", "name_ar": "جديد",
                                                            "global_role": "staff", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        from apps.accounts.models import User
        u = User.objects.get(username="newbie")
        self.assertTrue(u.must_change_password)
        # forced password change before anything else
        self.client.force_login(u)
        r = self.client.get(reverse("core:me"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:password_change"), r["Location"])

    def test_members_see_only_their_committees(self):
        self.login(self.chair)
        self.assertEqual(self.client.get(reverse("committees:detail", args=[self.com.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("committees:detail", args=[self.other.pk])).status_code, 403)
        self.assertTrue(P.can_view_committee(self.member, self.other))
        self.assertTrue(P.can_manage_meetings(self.member, self.other))     # secretary there
        self.assertFalse(P.can_manage_meetings(self.member, self.com))      # plain member here
        self.assertEqual(set(P.committee_ids_for(self.member)), {self.com.pk, self.other.pk})

    def test_member_cannot_schedule_or_approve(self):
        with self.assertRaises(PermissionDenied):
            CS.schedule_meeting(self.member, self.com, date_=self.today)
        m = CS.schedule_meeting(self.secretary, self.com, date_=self.today, invite=False)
        with self.assertRaises(PermissionDenied):
            CS.submit_mom(self.member, m, docx())
        CS.submit_mom(self.secretary, m, docx())
        for u in (self.member, self.secretary, self.outsider):
            with self.assertRaises(PermissionDenied):
                CS.review_mom(u, m, True)
        CS.review_mom(self.chair, m, True)
        with self.assertRaises(PermissionDenied):
            CS.review_mom(self.chair, m, True)       # management stage
        CS.review_mom(self.dean, m, True)

    def test_task_visibility_and_verification_rules(self):
        t = S.create_task(self.chair, title="Visible to committee", assignees=[self.member], committee=self.com)
        self.assertTrue(P.can_view_task(self.member, t))
        self.assertTrue(P.can_view_task(self.secretary, t))
        self.assertFalse(P.can_view_task(self.outsider, t))
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("governance:task", args=[t.pk])).status_code, 403)
        with self.assertRaises(PermissionDenied):
            S.update_progress(self.outsider, t, progress=50)        # not in this committee
        S.update_progress(self.secretary, t, progress=20)           # the secretary may record progress
        S.add_evidence(self.member, t, pdf())
        S.submit_for_verification(self.member, t)
        self.assertFalse(P.can_verify_task(self.member, t))
        self.assertFalse(P.can_verify_task(self.secretary, t))
        self.assertTrue(P.can_verify_task(self.chair, t))
        self.assertTrue(P.can_verify_task(self.vice, t))

    def test_chair_assigned_to_own_task_cannot_verify_it(self):
        t = S.create_task(self.vice, title="Chair's own", assignees=[self.chair], committee=self.com)
        S.add_evidence(self.chair, t, pdf())
        S.submit_for_verification(self.chair, t)
        self.assertFalse(P.can_verify_task(self.chair, t))
        with self.assertRaises(PermissionDenied):
            S.verify_task(self.chair, t, True)

    def test_verified_task_is_read_only(self):
        t = S.create_task(self.chair, title="Closed", assignees=[self.member], committee=self.com)
        S.add_evidence(self.member, t, pdf())
        S.submit_for_verification(self.member, t)
        S.verify_task(self.chair, t, True)
        with self.assertRaises((PermissionDenied, ValidationError)):
            S.add_evidence(self.member, t, pdf())
        with self.assertRaises((PermissionDenied, ValidationError)):
            S.edit_task(self.dean, t, title="changed")


class FileSecurity(Fixture):
    def test_upload_validation(self):
        validate_upload(pdf())
        with self.assertRaises(ValidationError):
            validate_upload(SimpleUploadedFile("evil.exe", b"MZ\x90\x00"))
        with self.assertRaises(ValidationError):           # wrong magic bytes
            validate_upload(SimpleUploadedFile("fake.pdf", b"<html>not a pdf</html>"))
        with self.assertRaises(ValidationError):
            validate_upload(SimpleUploadedFile("x.docx", b"%PDF-1.4"), allowed=["docx"])

    def test_files_served_only_through_permission_check(self):
        doc, v = store_file(pdf(), self.chair, title="Committee file", category="other", committee=self.com)
        url = reverse("documents:download", args=[v.pk])
        self.login(self.member)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_confidential_documents_are_management_only(self):
        doc, v = store_file(pdf(), self.vice, title="Confidential", category="other", committee=self.com)
        doc.is_confidential = True
        doc.save()
        self.login(self.chair)
        self.assertEqual(self.client.get(reverse("documents:download", args=[v.pk])).status_code, 403)
        self.login(self.dean)
        self.assertEqual(self.client.get(reverse("documents:download", args=[v.pk])).status_code, 200)

    def test_versioning_and_locking(self):
        doc, v1 = store_file(pdf("a.pdf"), self.chair, title="Plan", category="action_plan", committee=self.com)
        doc, v2 = store_file(pdf("b.pdf"), self.chair, title="Plan", category="action_plan", document=doc)
        self.assertEqual(doc.versions.count(), 2)
        self.assertEqual(doc.current.pk, v2.pk)
        self.assertEqual(len(v2.sha256), 64)
        doc.is_locked = True
        doc.save()
        with self.assertRaises(ValidationError):
            store_file(pdf("c.pdf"), self.chair, title="Plan", category="action_plan", document=doc)


class KPIFormulas(Fixture):
    def test_rates_do_not_grow_with_volume(self):
        start, end = self.year.start_date, self.year.end_date
        k0 = KPI.compute(self.com, start, end)
        self.assertIsNone(k0.score)            # nothing due -> no score, not zero
        for i in range(3):
            S.create_task(self.chair, title=f"T{i}", assignees=[self.member], committee=self.com,
                          due_date=self.past(1))
        k = KPI.compute(self.com, start, end)
        self.assertEqual((k.rates["task"].num, k.rates["task"].den), (0, 3))
        self.assertEqual(k.overdue_tasks, 3)
        self.assertEqual(k.health, "critical")
        self.assertLessEqual(k.score, 100)

    def test_meeting_compliance(self):
        CS.schedule_meeting(self.secretary, self.com, date_=self.past(2), invite=False)
        CS.schedule_meeting(self.secretary, self.com, date_=self.future(5), invite=False)
        k = KPI.compute(self.com, self.year.start_date, self.year.end_date)
        self.assertEqual(k.rates["meeting"].den, 1)      # only meetings due by today count


class PagesSmoke(Fixture):
    """Every main page renders in both languages for management and for a member."""

    def test_pages_render(self):
        t = S.create_task(self.chair, title="Smoke", assignees=[self.member], committee=self.com)
        m = CS.schedule_meeting(self.secretary, self.com, date_=self.today, invite=False)
        pages = ["core:me", "core:calendar", "core:notifications", "committees:list", "committees:meetings",
                 "committees:moms", "governance:decisions", "governance:tasks", "documents:archive",
                 "reports:index", "accounts:profile"]
        for lang in ("ar", "en"):
            for user in (self.vice, self.member):
                self.login(user)
                self.client.cookies["django_language"] = lang
                for p in pages:
                    r = self.client.get(reverse(p))
                    self.assertEqual(r.status_code, 200, f"{p} {user} {lang}")
                for url in (reverse("committees:detail", args=[self.com.pk]),
                            reverse("committees:meeting", args=[m.pk]),
                            reverse("governance:task", args=[t.pk]),
                            reverse("core:calendar_events")):
                    self.assertEqual(self.client.get(url).status_code, 200, url)
            self.login(self.vice)
            self.client.cookies["django_language"] = lang
            for p in ("core:overview", "core:followup", "core:admin_home", "documents:templates", "core:settings", "core:audit",
                      "accounts:users"):
                self.assertEqual(self.client.get(reverse(p)).status_code, 200, p)
            r = self.client.get(reverse("core:overview"))
            self.assertEqual(r.wsgi_request.LANGUAGE_CODE, lang)
