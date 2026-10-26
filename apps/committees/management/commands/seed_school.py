from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.committees import seed_data as D
from apps.committees.models import ActionPlanItem, Committee, CommitteeResponsibility, Membership
from apps.core.models import AcademicYear, AuditLog, Semester, SystemSettings

INITIAL_PASSWORD = "ChangeMe@2026"


class Command(BaseCommand):
    help = "Load the school's real committees, members and responsibilities (safe to run once on an empty database)."

    def add_arguments(self, parser):
        parser.add_argument("--admin-password", default=INITIAL_PASSWORD)

    @transaction.atomic
    def handle(self, *args, **opts):
        if Committee.objects.exists():
            self.stdout.write("Committees already exist — nothing loaded.")
            return
        SystemSettings.get()
        year, _ = AcademicYear.objects.get_or_create(
            name="2026-2027", defaults={"start_date": date(2026, 9, 1), "end_date": date(2027, 8, 31), "is_current": True})
        Semester.objects.get_or_create(academic_year=year, name_en="Fall", defaults={
            "name_ar": "الفصل الأول", "start_date": date(2026, 9, 14), "end_date": date(2027, 1, 31)})
        Semester.objects.get_or_create(academic_year=year, name_en="Spring", defaults={
            "name_ar": "الفصل الثاني", "start_date": date(2027, 2, 1), "end_date": date(2027, 6, 30)})

        if not User.objects.filter(username="admin").exists():
            a = User.objects.create_superuser("admin", "", opts["admin_password"])
            a.global_role, a.name_ar, a.name_en, a.must_change_password = "admin", "مدير النظام", "System Administrator", True
            a.save()
        users = {}
        for uname, (ar, en, role, pos) in D.PEOPLE.items():
            u, created = User.objects.get_or_create(username=uname, defaults={
                "name_ar": ar, "name_en": en, "global_role": role, "position": pos, "must_change_password": True})
            if created:
                u.set_password(INITIAL_PASSWORD)
                u.save()
            users[uname] = u

        for (code, ar, en, kind, chair, members, resp, plan, report) in D.COMMITTEES:
            c = Committee.objects.create(code=code, name_ar=ar, name_en=en, kind=kind, formed_on=date(2026, 9, 14),
                                         formation_reference=D.REFERENCE, report_frequency=report,
                                         meeting_frequency="monthly")
            for i, (rar, ren) in enumerate(resp, 1):
                CommitteeResponsibility.objects.create(committee=c, text_ar=rar, text_en=ren, order=i)
            for i, m in enumerate(members):
                role = Membership.Role.CHAIR if m == chair else Membership.Role.MEMBER
                Membership.objects.create(committee=c, user=users[m], role=role, order=i,
                                          note=D.MEMBER_NOTES.get((code, m), ""), start_date=date(2026, 9, 14))
            for title, due, note in plan:
                ActionPlanItem.objects.create(committee=c, academic_year=year, title=title, description=note,
                                              target_date=date.fromisoformat(due) if due else None,
                                              responsible=users[chair] if chair else None)
        AuditLog.objects.create(action="system.seed", message_en="Loaded the 12 committees from the formation letters of 14-9-2026",
                                message_ar="تحميل اللجان الاثنتي عشرة من قرارات التشكيل بتاريخ 14-9-2026")
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(D.COMMITTEES)} committees and {len(users)} people. Initial password for everyone: {INITIAL_PASSWORD}"))
