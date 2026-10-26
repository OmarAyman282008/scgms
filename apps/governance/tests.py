"""End-to-end acceptance scenario (specification §16) driven through the web views,
plus workflow rules for decisions, tasks, extensions and escalations."""
from django.urls import reverse

from apps.committees.models import MOM, Committee, Meeting
from apps.core.models import AuditLog, Notification, SystemSettings
from apps.core.testutils import BaseCase, docx, pdf
from apps.documents.models import Document
from apps.governance import services as S
from apps.governance.models import Decision, Escalation, ExtensionRequest, Task
from apps.reports import kpi as KPI
from apps.reports.reports import REPORTS


class AcceptanceScenario(BaseCase):
    """The 15 steps of the acceptance scenario, in order, as the real users would click them."""

    def test_full_decision_to_action_cycle(self):
        c = self.client

        # 1. System administrator establishes a committee ...
        self.login(self.admin)
        r = c.post(reverse("committees:new"), {
            "code": "TST", "name_ar": "لجنة الاختبار", "name_en": "Test Committee", "kind": "committee",
            "report_frequency": "monthly", "meeting_frequency": "monthly", "is_active": "on"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        com = Committee.objects.get(code="TST")

        # 2. ... and assigns chair, secretary and members.
        for u, role in [(self.chair, "chair"), (self.secretary, "secretary"),
                        (self.member, "member"), (self.member2, "member")]:
            self.assertEqual(c.post(reverse("committees:member_set", args=[com.pk]),
                                    {"user": u.pk, "role": role}).status_code, 302)
        self.assertEqual(com.chair, self.chair)
        self.assertEqual(com.secretary, self.secretary)

        # 3. The secretary schedules the monthly meeting (members are invited).
        self.login(self.secretary)
        r = c.post(reverse("committees:meeting_new"), {
            "committee": com.pk, "kind": "regular", "date": self.today.isoformat(), "start_time": "11:00",
            "venue": "Room 1", "agenda": "Item one\nItem two", "invite": "on"})
        self.assertEqual(r.status_code, 302)
        m = Meeting.objects.get(committee=com)
        self.assertEqual(m.agenda_items.count(), 2)
        self.assertTrue(Notification.objects.filter(recipient=self.member, kind="meeting_invite").exists())

        # 4. The secretary downloads the auto-filled MOM template.
        r = c.get(reverse("committees:mom_template", args=[m.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(b"".join(r.streaming_content).startswith(b"PK"))

        # attendance, then 5. upload of the completed MOM.
        r = c.post(reverse("committees:meeting_action", args=[m.pk]),
                   {"action": "attendance", **{f"att_{u.pk}": "present" for u in
                                                (self.chair, self.secretary, self.member, self.member2)}})
        self.assertEqual(r.status_code, 302)
        r = c.post(reverse("committees:meeting_action", args=[m.pk]),
                   {"action": "mom_upload", "file": docx(), "summary": "Discussion summary"})
        m.refresh_from_db()
        self.assertEqual(m.mom.status, MOM.Status.SUBMITTED)
        self.assertEqual(m.status, Meeting.Status.HELD)

        # 6. The chair approves the MOM -> it goes to management review.
        self.login(self.chair)
        c.post(reverse("committees:meeting_action", args=[m.pk]), {"action": "mom_approve"})
        m.mom.refresh_from_db()
        self.assertEqual(m.mom.status, MOM.Status.UNDER_REVIEW)

        # 7. The Vice-Dean gives the final approval; the MOM document is locked.
        self.login(self.vice)
        c.post(reverse("committees:meeting_action", args=[m.pk]), {"action": "mom_approve"})
        m.mom.refresh_from_db()
        self.assertEqual(m.mom.status, MOM.Status.APPROVED)
        self.assertTrue(m.mom.document.is_locked)

        # 8. The secretary registers the decisions from the approved minutes.
        self.login(self.secretary)
        r = c.post(reverse("governance:decision_new", args=[m.pk]), {
            "title": "Publish the student handbook", "description": "Full statement", "category": "academic",
            "priority": "high", "is_actionable": "on", "responsible": self.member.pk,
            "target_date": self.future(14).isoformat(), "required_evidence": "Published PDF"})
        self.assertEqual(r.status_code, 302)
        dec = Decision.objects.get(meeting=m)
        self.assertTrue(dec.code.startswith("DEC-") and "-TST-" in dec.code)

        # 9. The chair converts the decision into an assigned task.
        self.login(self.chair)
        r = c.post(reverse("governance:decision_convert", args=[dec.pk]), {
            "count": "1", "title_0": "Prepare and publish handbook", "assignees_0": [self.member.pk],
            "due_0": self.future(10).isoformat(), "priority_0": "high", "deliverables_0": "PDF"})
        self.assertEqual(r.status_code, 302)
        task = Task.objects.get(decision=dec)
        self.assertEqual(task.source, Task.Source.DECISION)
        dec.refresh_from_db()
        self.assertEqual(dec.status, "assigned")
        self.assertTrue(Notification.objects.filter(recipient=self.member, kind="task_assigned").exists())

        # 10. The member updates progress and uploads evidence.
        self.login(self.member)
        c.post(reverse("governance:task_action", args=[task.pk]), {"action": "progress", "progress": "60", "note": "Draft ready"})
        task.refresh_from_db()
        self.assertEqual((task.status, task.progress), ("in_progress", 60))
        # submitting without evidence is refused
        c.post(reverse("governance:task_action", args=[task.pk]), {"action": "submit"})
        task.refresh_from_db()
        self.assertEqual(task.status, "in_progress")
        c.post(reverse("governance:task_action", args=[task.pk]),
               {"action": "evidence", "files": pdf(), "description": "Handbook PDF"})
        self.assertEqual(task.evidence.count(), 1)

        # 11. The member submits for verification; the member cannot verify their own task.
        c.post(reverse("governance:task_action", args=[task.pk]), {"action": "submit", "note": "Done"})
        task.refresh_from_db()
        self.assertEqual(task.status, "submitted")
        r = c.post(reverse("governance:task_action", args=[task.pk]), {"action": "verify"})
        self.assertEqual(r.status_code, 403)

        # chair returns once, member resubmits with a new evidence round
        self.login(self.chair)
        c.post(reverse("governance:task_action", args=[task.pk]), {"action": "return", "comment": "Add the link"})
        task.refresh_from_db()
        self.assertEqual(task.status, "returned")
        self.login(self.member)
        c.post(reverse("governance:task_action", args=[task.pk]), {"action": "submit"})
        task.refresh_from_db()
        self.assertEqual(task.status, "returned", "a new round needs new evidence")
        c.post(reverse("governance:task_action", args=[task.pk]), {"action": "evidence", "files": pdf("v2.pdf")})
        c.post(reverse("governance:task_action", args=[task.pk]), {"action": "submit"})
        task.refresh_from_db()
        self.assertEqual(task.status, "submitted")

        # 12. The chair verifies; 13. the decision closes automatically.
        self.login(self.chair)
        c.post(reverse("governance:task_action", args=[task.pk]), {"action": "verify", "comment": "OK"})
        task.refresh_from_db()
        dec.refresh_from_db()
        self.assertEqual(task.status, "verified")
        self.assertEqual(dec.status, "implemented")
        self.assertIsNotNone(dec.closure_date)
        self.assertTrue(all(e.document.is_locked for e in task.evidence.all()))

        # 14. MOM and documents are archived, linked to the committee/meeting/decision/task.
        m.mom.refresh_from_db()
        self.assertEqual(m.mom.status, MOM.Status.ARCHIVED)
        self.assertTrue(Document.objects.filter(committee=com).exists() or Document.objects.filter(meeting=m).exists())
        self.login(self.vice)
        self.assertEqual(c.get(reverse("documents:archive") + f"?committee={com.pk}").status_code, 200)
        self.assertEqual(c.get(reverse("documents:export", args=[com.pk])).status_code, 200)

        # 15. Executive overview and reports reflect the closure.
        r = c.get(reverse("core:overview"))
        self.assertEqual(r.status_code, 200)
        k = KPI.compute(com, self.year.start_date, self.year.end_date)
        self.assertEqual((k.rates["decision"].num, k.rates["decision"].den), (1, 1))
        self.assertEqual(k.decisions_implemented, 1)
        for slug, *_ in REPORTS:
            for fmt in ("", "xlsx", "pdf"):
                r = c.get(reverse("reports:report", args=[slug]) + (f"?format={fmt}" if fmt else ""))
                self.assertEqual(r.status_code, 200, f"{slug} {fmt}")

        # every step left an audit trail
        actions = set(AuditLog.objects.values_list("action", flat=True))
        for a in ("mom.submit", "meeting.schedule"):
            self.assertIn(a, actions)
        self.assertGreater(AuditLog.objects.count(), 15)

    def test_urgent_management_task_flow(self):
        com = Committee.objects.create(code="URG", name_ar="لجنة", name_en="Urg")
        S_ = __import__("apps.committees.services", fromlist=["x"])
        S_.set_membership(self.admin, com, self.chair, "chair")
        S_.set_membership(self.admin, com, self.member, "member")
        c = self.client
        # staff cannot assign urgent tasks
        self.login(self.member)
        self.assertEqual(c.get(reverse("governance:task_new") + "?urgent=1").status_code, 403)
        # the Vice-Dean assigns one
        self.login(self.vice)
        r = c.post(reverse("governance:task_new") + "?urgent=1", {
            "urgent": "1", "committee": com.pk, "title": "Prepare accreditation file", "category": "quality",
            "priority": "urgent", "assignees": [self.member.pk], "due_date": self.future(3).isoformat(),
            "expected_deliverables": "File", "source_note": "Dean's directive"})
        self.assertEqual(r.status_code, 302)
        t = Task.objects.get(title="Prepare accreditation file")
        self.assertTrue(t.is_urgent and t.code.startswith("MGT-"))
        self.assertTrue(Notification.objects.filter(recipient=self.member, kind="task_urgent").exists())
        # it shows in the follow-up center
        r = c.get(reverse("core:followup"))
        self.assertContains(r, t.code)
        # assignee works and submits
        self.login(self.member)
        c.post(reverse("governance:task_action", args=[t.pk]), {"action": "evidence", "files": pdf()})
        c.post(reverse("governance:task_action", args=[t.pk]), {"action": "submit"})
        t.refresh_from_db()
        self.assertEqual(t.status, "submitted")
        # the committee chair cannot verify a management task
        self.login(self.chair)
        self.assertEqual(c.post(reverse("governance:task_action", args=[t.pk]), {"action": "verify"}).status_code, 403)
        # management verifies
        self.login(self.vice)
        c.post(reverse("governance:task_action", args=[t.pk]), {"action": "verify"})
        t.refresh_from_db()
        self.assertEqual(t.status, "verified")


class WorkflowRules(BaseCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.committees import services as CS
        cls.com = Committee.objects.create(code="WF", name_ar="لجنة سير العمل", name_en="Workflow")
        CS.set_membership(cls.admin, cls.com, cls.chair, "chair")
        CS.set_membership(cls.admin, cls.com, cls.secretary, "secretary")
        CS.set_membership(cls.admin, cls.com, cls.member, "member")

    def _approved_meeting(self):
        from apps.committees import services as CS
        m = CS.schedule_meeting(self.secretary, self.com, date_=self.today, invite=False)
        CS.submit_mom(self.secretary, m, docx())
        CS.review_mom(self.chair, m, True)
        CS.review_mom(self.vice, m, True)
        m.mom.refresh_from_db()
        return m

    def test_mom_return_and_resubmit(self):
        from apps.committees import services as CS
        m = CS.schedule_meeting(self.secretary, self.com, date_=self.today, invite=False)
        CS.submit_mom(self.secretary, m, docx())
        with self.assertRaises(Exception):
            CS.review_mom(self.chair, m, False, "")          # a comment is required
        CS.review_mom(self.chair, m, False, "Fix attendance")
        m.mom.refresh_from_db()
        self.assertEqual(m.mom.status, MOM.Status.REVISION)
        CS.submit_mom(self.secretary, m, docx("v2.docx"))
        m.mom.refresh_from_db()
        self.assertEqual(m.mom.status, MOM.Status.SUBMITTED)
        self.assertEqual(m.mom.document.versions.count(), 2)

    def test_chair_only_route_when_management_approval_disabled(self):
        s = SystemSettings.get()
        s.mom_requires_management_approval = False
        s.save()
        from apps.committees import services as CS
        m = CS.schedule_meeting(self.secretary, self.com, date_=self.today, invite=False)
        CS.submit_mom(self.secretary, m, docx())
        CS.review_mom(self.chair, m, True)
        m.mom.refresh_from_db()
        self.assertEqual(m.mom.status, MOM.Status.APPROVED)

    def test_no_decisions_before_approval_and_future_mom_refused(self):
        from django.core.exceptions import PermissionDenied, ValidationError
        from apps.committees import services as CS
        future = CS.schedule_meeting(self.secretary, self.com, date_=self.future(5), invite=False)
        with self.assertRaises(ValidationError):
            CS.submit_mom(self.secretary, future, docx())
        m = CS.schedule_meeting(self.secretary, self.com, date_=self.today, invite=False)
        with self.assertRaises(PermissionDenied):
            S.register_decision(self.secretary, m, title="Too early")

    def test_decision_closes_only_when_all_tasks_verified(self):
        m = self._approved_meeting()
        d = S.register_decision(self.secretary, m, title="Two-part decision")
        t1, t2 = S.convert_decision(self.chair, d, [
            {"title": "Part 1", "assignees": [self.member]}, {"title": "Part 2", "assignees": [self.secretary]}])
        for t, u in ((t1, self.member), (t2, self.secretary)):
            S.add_evidence(u, t, pdf())
            S.submit_for_verification(u, t)
        S.verify_task(self.chair, t1, True)
        d.refresh_from_db()
        self.assertEqual(d.status, "in_progress")
        S.verify_task(self.chair, t2, True)
        d.refresh_from_db()
        self.assertEqual(d.status, "implemented")

    def test_cancellation_needs_management(self):
        m = self._approved_meeting()
        d = S.register_decision(self.secretary, m, title="Will be cancelled")
        S.request_cancellation(self.chair, d, "No longer needed")
        d.refresh_from_db()
        self.assertNotEqual(d.status, "cancelled")
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            S.decide_cancellation(self.chair, d, True)
        S.decide_cancellation(self.dean, d, True, "Approved")
        d.refresh_from_db()
        self.assertEqual(d.status, "cancelled")

    def test_extension_and_escalation(self):
        t = S.create_task(self.chair, title="Lab inventory", assignees=[self.member], committee=self.com,
                          due_date=self.future(2))
        S.request_extension(self.member, t, self.future(20), "Supplier delay")
        req = ExtensionRequest.objects.get(task=t)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            S.request_extension(self.member, t, self.future(25), "again")
        S.decide_extension(self.chair, req, True)
        t.refresh_from_db()
        self.assertEqual(t.due_date, self.future(20))
        S.escalate(self.member, t, "Waiting for IT")
        esc = Escalation.objects.get(task=t)
        self.assertTrue(Notification.objects.filter(recipient=self.vice, kind="escalation").exists())
        S.resolve_escalation(self.vice, esc, "IT instructed")
        esc.refresh_from_db()
        self.assertEqual(esc.status, "resolved")

    def test_overdue_and_reminders_are_deduplicated(self):
        from apps.core.reminders import run_daily_reminders
        t = S.create_task(self.chair, title="Late task", assignees=[self.member], committee=self.com,
                          due_date=self.past(3))
        self.assertTrue(t.is_overdue)
        run_daily_reminders()
        n1 = Notification.objects.filter(recipient=self.member, kind="task_overdue").count()
        run_daily_reminders()
        n2 = Notification.objects.filter(recipient=self.member, kind="task_overdue").count()
        self.assertEqual(n1, 1)
        self.assertEqual(n1, n2)
