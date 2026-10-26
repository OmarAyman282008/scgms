import csv
import io
import mimetypes
import re
import zipfile
from datetime import date

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.committees.models import Committee, Meeting
from apps.core import permissions as P
from apps.core.models import AcademicYear
from apps.core.services import audit
from apps.governance.models import Decision, Evidence, Task

from .models import Document, DocumentTemplate, DocumentVersion
from .services import store_file, validate_upload

INLINE = {"pdf", "png", "jpg", "jpeg", "txt"}


def _visible_docs(user):
    qs = Document.objects.all()
    if P.sees_everything(user):
        return qs
    ids = P.committee_ids_for(user)
    return qs.filter(is_confidential=False).filter(Q(committee_id__in=ids) | Q(task__assignments__user=user)).distinct()


@login_required
def archive(request):
    years = AcademicYear.objects.all()
    year = AcademicYear.objects.filter(pk=request.GET.get("year")).first() or AcademicYear.current()
    ids = P.committee_ids_for(request.user)
    committees = Committee.objects.filter(id__in=ids)
    c = committees.filter(pk=request.GET.get("committee")).first()
    g = request.GET
    searching = any(g.get(k) for k in ("q", "category", "from", "to", "decision"))
    ctx = {"years": years, "year": year, "committees": committees, "c": c, "g": g, "searching": searching,
           "categories": Document.Category.choices}
    docs = _visible_docs(request.user).select_related("committee", "meeting", "decision", "task").prefetch_related("versions")
    if searching:
        if g.get("committee"):
            docs = docs.filter(committee_id=g["committee"])
        if g.get("category"):
            docs = docs.filter(category=g["category"])
        if g.get("q"):
            docs = docs.filter(Q(title__icontains=g["q"]) | Q(versions__original_name__icontains=g["q"])).distinct()
        if g.get("decision"):
            docs = docs.filter(Q(decision__code__icontains=g["decision"]) | Q(task__decision__code__icontains=g["decision"]))
        if g.get("from"):
            docs = docs.filter(created_at__date__gte=g["from"])
        if g.get("to"):
            docs = docs.filter(created_at__date__lte=g["to"])
        ctx["page"] = Paginator(docs, 50).get_page(g.get("page"))
    elif c:
        cd = docs.filter(committee=c, academic_year=year) if year else docs.filter(committee=c)
        by_cat = {}
        for d in cd:
            by_cat.setdefault(d.category, []).append(d)
        meetings = c.meetings.filter(academic_year=year).select_related("mom").prefetch_related(
            "agenda_items", "decisions__tasks").order_by("number") if year else []
        mrows = []
        for m in meetings:
            mdocs = [d for d in cd if d.meeting_id == m.id]
            mrows.append({"m": m, "agenda": [d for d in mdocs if d.category == "agenda"],
                          "mom": [d for d in mdocs if d.category == "mom"],
                          "decisions": list(m.decisions.all()),
                          "tasks": [t for dec in m.decisions.all() for t in dec.tasks.all()],
                          "evidence": [d for d in mdocs if d.category == "evidence"]})
        ctx.update(by_cat=by_cat, mrows=mrows,
                   implemented=c.decisions.filter(status="implemented", meeting__academic_year=year) if year else [],
                   memberships=c.memberships.select_related("user"),
                   can_upload=P.can_manage_meetings(request.user, c))
    else:
        counts = {}
        for row in docs.filter(academic_year=year).values_list("committee_id", flat=True) if year else []:
            counts[row] = counts.get(row, 0) + 1
        ctx["counts"] = [(x, counts.get(x.id, 0)) for x in committees]
    return render(request, "documents/archive.html", ctx)


@login_required
def document_detail(request, pk):
    d = get_object_or_404(Document.objects.select_related("committee", "meeting", "decision", "task"), pk=pk)
    P.require(P.can_view_document(request.user, d))
    can_version = (not d.is_locked) and (
        (d.committee_id and P.can_manage_meetings(request.user, d.committee)) or
        (d.task_id and P.can_update_task(request.user, d.task)) or P.is_exec(request.user))
    return render(request, "documents/detail.html", {"d": d, "versions": d.versions.select_related("uploaded_by"),
                                                     "can_version": can_version})


@login_required
def download(request, vid):
    v = get_object_or_404(DocumentVersion.objects.select_related("document"), pk=vid)
    P.require(P.can_view_document(request.user, v.document))
    inline = request.GET.get("inline") == "1" and v.extension in INLINE
    ctype = mimetypes.guess_type(v.original_name)[0] or "application/octet-stream"
    resp = FileResponse(v.file.open("rb"), as_attachment=not inline, filename=v.original_name, content_type=ctype)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return resp


class UploadForm(forms.Form):
    committee = forms.ModelChoiceField(queryset=Committee.objects.none())
    category = forms.ChoiceField(choices=[c for c in Document.Category.choices if c[0] not in ("mom", "evidence")])
    title = forms.CharField(max_length=300)
    meeting = forms.ModelChoiceField(queryset=Meeting.objects.none(), required=False)
    is_confidential = forms.BooleanField(required=False)
    file = forms.FileField()


@login_required
@require_POST
def upload(request):
    c = get_object_or_404(Committee, pk=request.POST.get("committee"))
    P.require(P.can_manage_meetings(request.user, c) or P.is_exec(request.user))
    cat = request.POST.get("category", "other")
    if cat in ("mom", "evidence") or cat not in dict(Document.Category.choices):
        cat = "other"
    meeting = c.meetings.filter(pk=request.POST.get("meeting") or 0).first()
    try:
        doc, v = store_file(request.FILES.get("file"), request.user, title=request.POST.get("title") or
                            request.FILES["file"].name, category=cat, committee=c, meeting=meeting,
                            is_confidential=bool(request.POST.get("is_confidential")) and P.is_exec(request.user))
        audit(request.user, "document.upload", f"Uploaded {doc.title} ({cat}) to {c.name_en}",
              f"رفع مستند {doc.title} إلى {c.name_ar}", doc, c, request)
        messages.success(request, _("File uploaded."))
    except (ValidationError, KeyError) as e:
        messages.error(request, " ".join(getattr(e, "messages", [str(e)])))
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith("/") else f"/archive/?committee={c.pk}")


@login_required
@require_POST
def new_version(request, pk):
    d = get_object_or_404(Document, pk=pk)
    P.require(P.can_view_document(request.user, d))
    allowed = ((d.committee_id and P.can_manage_meetings(request.user, d.committee)) or
               (d.task_id and P.can_update_task(request.user, d.task)) or P.is_exec(request.user))
    P.require(allowed and d.category != "mom")  # MOM versions go through the approval workflow
    try:
        _doc, v = store_file(request.FILES.get("file"), request.user, title=d.title, category=d.category, document=d,
                             note=request.POST.get("note", ""))
        audit(request.user, "document.version", f"New version {v.version_no} of {d.title}",
              f"إصدار جديد {v.version_no} من {d.title}", d, d.committee, request)
        messages.success(request, _("New version uploaded."))
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("documents:detail", pk=d.pk)


def _safe(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip()[:80] or "file"


@login_required
def export_committee(request, pk):
    """ZIP of a committee's whole record for one academic year, in the archive's folder structure."""
    c = get_object_or_404(Committee, pk=pk)
    P.require(P.can_view_committee(request.user, c))
    year = AcademicYear.objects.filter(pk=request.GET.get("year")).first() or AcademicYear.current()
    docs = _visible_docs(request.user).filter(committee=c)
    if year:
        docs = docs.filter(academic_year=year)
    folder_names = {"formation": "01 Formation & Membership", "action_plan": "02 Annual Action Plan",
                    "report": "04 Periodic Reports", "decision": "05 Approved Decisions",
                    "correspondence": "06 Correspondence", "annual_report": "07 Annual Performance Report",
                    "other": "08 Other"}
    sub = {"agenda": "Agenda", "mom": "Approved MOM", "evidence": "Implementation Evidence"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        root = f"{year.name if year else 'all'}/{_safe(c.name_en)}"
        for d in docs.select_related("meeting", "task").prefetch_related("versions"):
            if d.meeting_id:
                base = f"{root}/03 Monthly Meetings/Meeting {d.meeting.number:02d} ({d.meeting.date})/{sub.get(d.category, 'Other')}"
            else:
                base = f"{root}/{folder_names.get(d.category, '08 Other')}"
            for v in d.versions.all():
                try:
                    with v.file.open("rb") as fh:
                        z.writestr(f"{base}/{d.id}_v{v.version_no}_{_safe(v.original_name)}", fh.read())
                except FileNotFoundError:
                    continue
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["decision", "title", "meeting", "status", "target", "closed", "task", "task status", "assignees",
                    "verified by", "verified at", "evidence files"])
        decs = c.decisions.filter(meeting__academic_year=year) if year else c.decisions.all()
        for dcs in decs.select_related("meeting").prefetch_related("tasks__assignments__user", "tasks__evidence"):
            tasks = list(dcs.tasks.all()) or [None]
            for t in tasks:
                w.writerow([dcs.code, dcs.title, dcs.meeting.number, dcs.status, dcs.target_date or "",
                            dcs.closure_date.date() if dcs.closure_date else "",
                            t.code if t else "", t.status if t else "",
                            "; ".join(a.user.username for a in t.assignments.all()) if t else "",
                            t.verified_by.username if t and t.verified_by else "",
                            t.verified_at.date() if t and t.verified_at else "", t.evidence.count() if t else 0])
        z.writestr(f"{root}/decisions_and_tasks_register.csv", "﻿" + out.getvalue())
    buf.seek(0)
    audit(request.user, "archive.export", f"Exported records of {c.name_en}", f"تصدير سجلات {c.name_ar}", c, c, request)
    return FileResponse(buf, as_attachment=True, filename=f"{c.code}_{year.name if year else 'all'}_records.zip")


@login_required
def templates_view(request):
    P.require(P.sees_everything(request.user))
    if request.method == "POST":
        P.require(P.is_admin(request.user) or P.is_exec(request.user))
        f = request.FILES.get("file")
        try:
            validate_upload(f, ["docx"])
            last = DocumentTemplate.objects.filter(kind="mom").order_by("-version_no").first()
            t = DocumentTemplate(kind="mom", name=request.POST.get("name") or f.name, version_no=(last.version_no + 1) if last else 1,
                                 note=request.POST.get("note", ""), uploaded_by=request.user, is_active=True)
            t.file.save(f.name, f, save=False)
            t.save()
            audit(request.user, "template.upload", f"Uploaded MOM template v{t.version_no}",
                  f"رفع نموذج محضر إصدار {t.version_no}", t, request=request)
            messages.success(request, _("Template uploaded and activated."))
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect("documents:templates")
    if request.GET.get("activate"):
        t = get_object_or_404(DocumentTemplate, pk=request.GET["activate"])
        t.is_active = True
        t.save()
        audit(request.user, "template.activate", f"Activated MOM template v{t.version_no}",
              f"تفعيل نموذج المحضر إصدار {t.version_no}", t, request=request)
        return redirect("documents:templates")
    return render(request, "documents/templates.html", {"templates": DocumentTemplate.objects.all()})


@login_required
def template_download(request, pk=None):
    from .momtemplate import DEFAULT_TEMPLATE
    if pk:
        t = get_object_or_404(DocumentTemplate, pk=pk)
        return FileResponse(t.file.open("rb"), as_attachment=True, filename=f"MOM_template_v{t.version_no}.docx")
    return FileResponse(open(DEFAULT_TEMPLATE, "rb"), as_attachment=True, filename="MOM_template_default.docx")
