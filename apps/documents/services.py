import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import Document, DocumentVersion

MAGIC = {
    "pdf": [b"%PDF"],
    "docx": [b"PK\x03\x04"], "xlsx": [b"PK\x03\x04"], "pptx": [b"PK\x03\x04"], "zip": [b"PK\x03\x04"],
    "doc": [b"\xd0\xcf\x11\xe0"], "xls": [b"\xd0\xcf\x11\xe0"],
    "png": [b"\x89PNG"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"],
}


def validate_upload(f, allowed=None):
    """Extension whitelist, size limit and a file-signature check, so a renamed
    executable cannot be stored as a 'PDF'."""
    if not f:
        raise ValidationError(_("Choose a file first."))
    ext = os.path.splitext(f.name)[1].lower().lstrip(".")
    allowed = allowed or settings.ALLOWED_UPLOAD_EXTENSIONS
    if ext not in allowed:
        raise ValidationError(_("File type .%(ext)s is not allowed. Allowed: %(list)s")
                              % {"ext": ext, "list": ", ".join(allowed)})
    if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(_("File is larger than %(mb)s MB.") % {"mb": settings.MAX_UPLOAD_MB})
    if ext in MAGIC:
        head = f.read(8)
        f.seek(0)
        if not any(head.startswith(m) for m in MAGIC[ext]):
            raise ValidationError(_("The file content does not match its .%(ext)s extension.") % {"ext": ext})
    return ext


@transaction.atomic
def store_file(f, user, *, title, category, document=None, note="", allowed=None, **links):
    """Create a Document (or a new version of an existing one) from an uploaded file."""
    validate_upload(f, allowed)
    if document is None:
        if links.get("meeting") and not links.get("committee"):
            links["committee"] = links["meeting"].committee
        if links.get("task") and not links.get("committee"):
            links["committee"] = links["task"].committee
        if links.get("decision") and not links.get("committee"):
            links["committee"] = links["decision"].committee
        if not links.get("academic_year"):
            from apps.core.models import AcademicYear
            m = links.get("meeting")
            links["academic_year"] = m.academic_year if m else AcademicYear.current()
        document = Document.objects.create(title=title[:300], category=category, created_by=user, **links)
    elif document.is_locked:
        raise ValidationError(_("This record is approved and locked; it cannot be replaced."))
    last = document.versions.order_by("-version_no").first()
    v = DocumentVersion(document=document, version_no=(last.version_no + 1) if last else 1,
                        original_name=os.path.basename(f.name)[:255], size=f.size, uploaded_by=user, note=note)
    v.file.save(f.name, f, save=False)
    v.sha256 = v.compute_hash()
    v.save()
    return document, v
