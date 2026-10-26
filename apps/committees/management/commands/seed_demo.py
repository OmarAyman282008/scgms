"""Load DEMONSTRATION meetings, minutes, decisions, tasks, evidence and reviews on top
of the real committees, so every screen can be explored. Everything created here is
flagged is_demo and removed by `python manage.py purge_demo`.

The demo history runs through the normal workflow services (with a simulated clock),
so it exercises exactly the same rules as real use."""
import io
import os
from datetime import datetime, time, timedelta
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.committees import services as CS
from apps.committees.models import ActionPlanItem, Committee, Meeting
from apps.core.models import AuditLog, Comment
from apps.core.services import silence
from apps.documents.models import Document
from apps.governance import services as GS
from apps.governance.models import Decision, Task


def pdf_bytes(title, lines):
    """A small evidence PDF (Arabic shaped) so previews work."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    font = os.path.join(settings.BASE_DIR, "static", "fonts", "DejaVuSans.ttf")
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", font))
    shape = lambda t: get_display(arabic_reshaper.reshape(t))  # noqa: E731
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont("DejaVu", 16)
    c.drawRightString(w - 50, h - 70, shape(title))
    c.setFont("DejaVu", 11)
    y = h - 110
    for line in lines:
        c.drawRightString(w - 50, y, shape(line))
        y -= 20
    c.setFont("DejaVu", 9)
    c.drawString(50, 40, "DEMO evidence file - SCGMS")
    c.save()
    return buf.getvalue()


def upload(name, data, ctype="application/pdf"):
    return SimpleUploadedFile(name, data, content_type=ctype)


class Command(BaseCommand):
    help = "Load demonstration data (flagged DEMO; remove with purge_demo)."

    def handle(self, *args, **opts):
        if not Committee.objects.filter(code="SAE").exists():
            raise CommandError("Run `python manage.py seed_school` first.")
        if Meeting.objects.filter(is_demo=True).exists():
            self.stdout.write("Demo data already loaded.")
            return
        marks = {m: m.objects.order_by("-id").values_list("id", flat=True).first() or 0
                 for m in (Meeting, Decision, Task, Document, AuditLog, ActionPlanItem, Comment)}
        with silence(), transaction.atomic():
            self.build()
            for m, last in marks.items():
                qs = m.objects.filter(id__gt=last)
                if m is AuditLog:
                    for a in qs:
                        a.message_en = "[DEMO] " + a.message_en
                        a.message_ar = "[DEMO] " + a.message_ar
                        a.save(update_fields=["message_en", "message_ar"])
                elif m is not Comment:
                    qs.update(is_demo=True)
        self.stdout.write(self.style.SUCCESS("Demo data loaded. Remove it any time with: python manage.py purge_demo"))

    # ------------------------------------------------------------------
    def build(self):
        U = {u.username: u for u in User.objects.all()}
        alias = {"research": "RES", "library": "LIB", "student_affairs": "SAE", "labs": "LAB", "followup": "FUP",
                 "activities": "ACT", "advising": "ADV", "quality": "QUA", "control": "CTL", "training": "TRN",
                 "agreements": "AGR", "departments": "HOD"}
        C = {k: Committee.objects.get(code=v) for k, v in alias.items()}
        tz = timezone.get_current_timezone()

        def clock(day, hour=10, minute=0):
            dt = timezone.make_aware(datetime.combine(day, time(hour, minute)), tz)
            return mock.patch.object(timezone, "now", return_value=dt)

        d = lambda s: datetime.strptime(s, "%Y-%m-%d").date()  # noqa: E731

        def meeting(code, day, actor, agenda, attend=None, t="11:00"):
            with clock(d(day) - timedelta(days=7), 9):
                m = CS.schedule_meeting(U[actor], C[code], date_=d(day), start_time=time(*map(int, t.split(":"))),
                                        venue="قاعة اجتماعات الكلية", agenda=agenda)
            if attend is not None:
                with clock(d(day), 13):
                    st = {mb.user_id: "present" for mb in C[code].active_memberships()}
                    st.update({U[k].id: v for k, v in attend.items()})
                    CS.record_attendance(U[actor], m, st)
            return m

        def mom(m, actor, day, summary, approver=None, approve_day=None):
            from apps.documents.momtemplate import render
            with clock(d(day), 12):
                data = render(m).getvalue()
                CS.submit_mom(U[actor], m, upload(f"MOM_{m.committee.code}_{m.number}.docx", data,
                                                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                              summary)
            if approver:
                m.mom.refresh_from_db()
                with clock(d(approve_day), 14):
                    CS.review_mom(U[approver], m, True, "معتمد")
            m.mom.refresh_from_db()
            return m

        def decision(m, actor, day, title, **kw):
            with clock(d(day), 15):
                return GS.register_decision(U[actor], m, title=title, **kw)

        def tasks(dec, actor, day, specs):
            with clock(d(day), 16):
                return GS.convert_decision(U[actor], dec, specs)

        def progress(t, actor, day, pct, note, blocked=False):
            with clock(d(day), 11):
                GS.update_progress(U[actor], t, progress=pct, note=note, blocked=blocked)

        def evidence(t, actor, day, title, lines):
            with clock(d(day), 12):
                GS.add_evidence(U[actor], t, upload(f"{t.code}_evidence.pdf", pdf_bytes(title, lines)), title)

        def submit(t, actor, day, note=""):
            with clock(d(day), 13):
                GS.submit_for_verification(U[actor], t, note)

        def verify(t, actor, day, ok=True, comment=""):
            with clock(d(day), 10):
                t.refresh_from_db()
                GS.verify_task(U[actor], t, ok, comment)

        # ---------------- Student Affairs & Examinations (chair: Vice-Dean) -------------
        sa1 = meeting("student_affairs", "2026-09-22", "gkhoriba",
                      ["اعتماد خطة إعداد دليل الطالب", "تفعيل الحضور الإلكتروني على LMS", "جدول الامتحانات النصفية"],
                      attend={"sali": "excused"})
        mom(sa1, "gkhoriba", "2026-09-24", "تمت مناقشة خطة دليل الطالب وتفعيل الحضور الإلكتروني.",
            approver="mkhafagy", approve_day="2026-09-28")
        d1 = decision(sa1, "gkhoriba", "2026-09-28", "إعداد دليل الطالب ونشره على موقع الكلية",
                      description="إعداد دليل الطالب للعام 2026/2027 باللغتين ونشره على موقع الكلية ونظام LMS.",
                      category="student_affairs", priority="high", responsible=U["eragaey"],
                      target_date=d("2026-10-14"), required_evidence="نسخة الدليل النهائية + رابط النشر")
        t1, t2 = tasks(d1, "gkhoriba", "2026-09-28", [
            {"title": "إعداد المحتوى النهائي لدليل الطالب", "assignees": [U["eragaey"]], "due_date": d("2026-10-10"),
             "priority": "high", "expected_deliverables": "ملف الدليل PDF"},
            {"title": "نشر الدليل على موقع الكلية وLMS", "assignees": [U["mkhaled"]], "due_date": d("2026-10-20"),
             "priority": "high", "expected_deliverables": "لقطة شاشة لصفحة النشر"}])
        progress(t1, "eragaey", "2026-10-02", 50, "تم الانتهاء من الفصول الأربعة الأولى")
        evidence(t1, "eragaey", "2026-10-08", "دليل الطالب — النسخة النهائية", ["دليل الطالب 2026/2027", "النسخة النهائية"])
        submit(t1, "eragaey", "2026-10-08", "تم الانتهاء من الدليل")
        verify(t1, "gkhoriba", "2026-10-09")
        progress(t2, "mkhaled", "2026-10-15", 60, "بانتظار رفع الملف من وحدة الموقع الإلكتروني")

        d2 = decision(sa1, "gkhoriba", "2026-09-28", "تفعيل تسجيل الحضور على LMS لجميع المقررات",
                      category="academic", priority="medium", responsible=U["asarhan"], target_date=d("2026-10-10"),
                      required_evidence="تقرير من LMS بنسبة المقررات المفعلة")
        (t3,) = tasks(d2, "gkhoriba", "2026-09-28", [
            {"title": "تفعيل أداة الحضور في مقررات الفصل الأول", "assignees": [U["asarhan"]],
             "due_date": d("2026-10-10"), "priority": "medium", "expected_deliverables": "تقرير LMS"}])
        progress(t3, "asarhan", "2026-10-05", 80, "تم تفعيل 34 مقررًا من 40")
        evidence(t3, "asarhan", "2026-10-09", "تقرير تفعيل الحضور", ["تم تفعيل الحضور في 40 مقررًا من 40"])
        submit(t3, "asarhan", "2026-10-09")
        verify(t3, "gkhoriba", "2026-10-11", ok=False, comment="يرجى إرفاق تقرير LMS الرسمي وليس ملخصًا")
        evidence(t3, "asarhan", "2026-10-16", "تقرير LMS الرسمي", ["تقرير النظام: 40/40 مقررًا"])
        submit(t3, "asarhan", "2026-10-16", "تم إرفاق التقرير الرسمي")

        decision(sa1, "gkhoriba", "2026-09-28", "العلم بجدول الامتحانات النصفية المعتمد", is_actionable=False,
                 category="student_affairs", priority="low")

        sa2 = meeting("student_affairs", "2026-10-13", "gkhoriba", ["متابعة قرارات الاجتماع السابق", "الأعذار والالتماسات"])
        with clock(d("2026-10-15"), 12):
            from apps.documents.momtemplate import render
            CS.submit_mom(U["mkhafagy"], sa2, upload("MOM_student_affairs_2.docx", render(sa2).getvalue(),
                                                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                          "متابعة نشر الدليل؛ اعتماد 12 عذرًا مرضيًا.")
        meeting("student_affairs", "2026-11-10", "gkhoriba", ["متابعة القرارات المفتوحة"])

        # ---------------- Academic Advising --------------------------------------------
        ad1 = meeting("advising", "2026-09-24", "amamdouh", ["توزيع المرشدين", "آلية الطلاب المتعثرين"])
        mom(ad1, "amamdouh", "2026-09-27", "تم توزيع المرشدين ومناقشة آلية التعامل مع المتعثرين.",
            approver="gkhoriba", approve_day="2026-09-29")
        d4 = decision(ad1, "amamdouh", "2026-09-29", "توزيع المرشدين الأكاديميين على الطلاب",
                      category="academic", priority="high", responsible=U["asarhan"], target_date=d("2026-09-30"),
                      required_evidence="كشف التوزيع المعتمد")
        (t4,) = tasks(d4, "amamdouh", "2026-09-29", [
            {"title": "إعداد كشف توزيع المرشدين ونشره", "assignees": [U["asarhan"], U["smabrouk"]],
             "due_date": d("2026-09-30"), "priority": "high", "expected_deliverables": "كشف التوزيع"}])
        evidence(t4, "smabrouk", "2026-09-30", "كشف توزيع المرشدين", ["كشف توزيع المرشدين الأكاديميين", "عدد الطلاب: 412"])
        submit(t4, "asarhan", "2026-09-30")
        verify(t4, "amamdouh", "2026-10-01")
        d5 = decision(ad1, "amamdouh", "2026-09-29", "وضع آلية للتعامل مع الطلاب المتعثرين",
                      category="academic", priority="medium", responsible=U["sahmed"], target_date=d("2026-10-25"),
                      required_evidence="الآلية المعتمدة + كشف الطلاب المستهدفين")
        (t5,) = tasks(d5, "amamdouh", "2026-09-29", [
            {"title": "إعداد آلية الطلاب المتعثرين وكشف المستهدفين", "assignees": [U["sahmed"]],
             "due_date": d("2026-10-25"), "priority": "medium"}])
        progress(t5, "sahmed", "2026-10-12", 40, "بانتظار بيانات الدرجات من الكنترول", blocked=True)
        with clock(d("2026-10-14"), 9):
            GS.escalate(U["amamdouh"], t5, "لم تصل بيانات درجات الفصل السابق من الكنترول؛ نحتاج توجيهًا للكنترول بإرسالها.")
        meeting("advising", "2026-10-29", "amamdouh", ["متابعة آلية المتعثرين"])

        # ---------------- Quality: minutes returned for revision -------------------------
        q1 = meeting("quality", "2026-09-29", "eragaey", ["توصيف البرامج", "نماذج الاستبيانات"])
        mom(q1, "eragaey", "2026-10-02", "مناقشة توصيف البرامج.")
        with clock(d("2026-10-04"), 10):
            CS.review_mom(U["gkhoriba"], q1, False, "يرجى إضافة أسماء المسؤولين ومواعيد التنفيذ لكل توصية.")

        # ---------------- Labs: minutes overdue -------------------------------------------
        meeting("labs", "2026-09-27", "melanany", ["حصر البرامج المطلوبة", "الأعطال"], attend={"znabil": "absent"})

        # ---------------- Activities: extension request -----------------------------------
        ac1 = meeting("activities", "2026-10-06", "agaber", ["خطة الأنشطة", "ندوة الذكاء الاصطناعي"])
        mom(ac1, "agaber", "2026-10-08", "اعتماد خطة الأنشطة للفصل الأول.", approver="gkhoriba", approve_day="2026-10-09")
        d6 = decision(ac1, "agaber", "2026-10-09", "تنظيم ندوة عن الذكاء الاصطناعي التوليدي للطلاب",
                      category="student_affairs", priority="medium", responsible=U["nelmasry"],
                      target_date=d("2026-11-05"), required_evidence="صور الندوة + كشف الحضور")
        (t6,) = tasks(d6, "agaber", "2026-10-09", [
            {"title": "تنظيم ندوة الذكاء الاصطناعي التوليدي", "assignees": [U["nelmasry"], U["bhassan"]],
             "due_date": d("2026-11-05"), "priority": "medium", "expected_deliverables": "صور + كشف حضور"}])
        progress(t6, "nelmasry", "2026-10-15", 30, "تم التواصل مع المتحدثين")
        with clock(d("2026-10-18"), 10):
            GS.request_extension(U["nelmasry"], t6, d("2026-11-15"), "تأجيل موعد المتحدث الرئيسي إلى منتصف نوفمبر")

        # ---------------- Research ----------------------------------------------------------
        r1 = meeting("research", "2026-10-08", "mkhafagy", ["المشروعات البحثية الممولة", "المدرسة الصيفية"])
        mom(r1, "mkhafagy", "2026-10-11", "مناقشة فرص التمويل المتاحة.", approver="gkhoriba", approve_day="2026-10-12")
        d7 = decision(r1, "mkhafagy", "2026-10-12", "إعداد مقترح مشروع بحثي ممول لتقديمه لأكاديمية البحث العلمي",
                      category="academic", priority="high", responsible=U["gkhoriba"], target_date=d("2026-12-15"),
                      required_evidence="المقترح المقدم + إيصال التقديم")
        tasks(d7, "mkhafagy", "2026-10-12", [
            {"title": "كتابة مسودة المقترح البحثي", "assignees": [U["gkhoriba"], U["znabil"]],
             "due_date": d("2026-11-30"), "priority": "high"},
            {"title": "تجهيز الميزانية والجدول الزمني للمقترح", "assignees": [U["rhamed"]],
             "due_date": d("2026-12-05"), "priority": "medium"}])

        # ---------------- Other calendar items ---------------------------------------------
        meeting("library", "2026-10-28", "nmokhtar", ["اقتراح الدوريات العلمية"])
        cm = meeting("control", "2026-10-05", "gosman", ["كشوف الامتحانات"])
        with clock(d("2026-10-04"), 9):
            CS.cancel_meeting(U["gosman"], cm, "تعارض مع اجتماع مجلس الجامعة")

        # ---------------- Urgent management assignments -----------------------------------
        with clock(d("2026-10-12"), 9):
            u1 = GS.create_task(U["gkhoriba"], source="management", is_urgent=True, committee=C["student_affairs"],
                                title="تقرير إحصائي بأعداد الطلاب المقيدين لمجلس الجامعة",
                                description="مطلوب لمجلس الجامعة القادم: أعداد الطلاب حسب البرنامج والمستوى.",
                                assignees=[U["mkhaled"]], due_date=d("2026-10-19"), priority="urgent",
                                expected_deliverables="ملف Excel + ملخص صفحة واحدة")
        progress(u1, "mkhaled", "2026-10-16", 50, "تم تجميع بيانات المستوى الأول والثاني")
        with clock(d("2026-10-20"), 9):
            GS.add_comment(U["gkhoriba"], "المطلوب قبل الخميس — يرجى الإسراع", task=u1)
        with clock(d("2026-10-15"), 9):
            GS.create_task(U["mkhafagy"], source="management", is_urgent=True, committee=C["quality"],
                           title="مراجعة توصيف المقررات قبل زيارة المراجعين", assignees=[U["eragaey"], U["aafifi"]],
                           due_date=d("2026-11-01"), priority="urgent",
                           expected_deliverables="قائمة المقررات المراجعة وملاحظاتها")
            u3 = GS.create_task(U["gkhoriba"], source="management", is_urgent=True, committee=C["labs"],
                                title="حصر احتياجات المعامل من التراخيص للفصل الثاني", assignees=[U["rabouzeid"]],
                                due_date=d("2026-10-25"), priority="high", expected_deliverables="قائمة التراخيص والتكلفة")
        evidence(u3, "rabouzeid", "2026-10-19", "قائمة التراخيص المطلوبة", ["MATLAB — 60 ترخيص", "Microsoft Azure Dev Tools"])
        submit(u3, "rabouzeid", "2026-10-19")
        with clock(d("2026-10-01"), 9):
            u4 = GS.create_task(U["mkhafagy"], source="management", is_urgent=True, committee=C["agreements"],
                                title="إعداد ملف الكلية لزيارة رئيس الجامعة", assignees=[U["rhamed"]],
                                due_date=d("2026-10-08"), priority="urgent")
        evidence(u4, "rhamed", "2026-10-07", "ملف زيارة رئيس الجامعة", ["العرض التقديمي", "ملخص الإنجازات"])
        submit(u4, "rhamed", "2026-10-07")
        verify(u4, "mkhafagy", "2026-10-08", comment="ممتاز")

        # mark one plan item as converted
        item = ActionPlanItem.objects.filter(committee=C["student_affairs"]).first()
        if item:
            item.status = "tasked"
            item.save(update_fields=["status"])
