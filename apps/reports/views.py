from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import get_language

from apps.core import permissions as P
from apps.core.filters import Filters
from apps.core.services import audit
from apps.governance.models import Decision, Priority

from . import exports
from .kpi import KPI_DEFS
from .reports import REPORTS, build


@login_required
def index(request):
    en = (get_language() or "ar").startswith("en")
    items = [(s, e if en else a) for s, e, a in REPORTS]
    return render(request, "reports/index.html", {"items": items, "kpi_defs": KPI_DEFS})


@login_required
def report(request, slug):
    if slug not in [r[0] for r in REPORTS]:
        raise Http404
    f = Filters(request)
    if not P.sees_everything(request.user) and not f.all_committees:
        P.require(False)
    rep = build(slug, f)
    rtl = not (get_language() or "ar").startswith("en")
    fmt = request.GET.get("format")
    if fmt in ("xlsx", "pdf"):
        audit(request.user, "report.export", f"Exported {slug} ({fmt})", f"تصدير تقرير {slug} ({fmt})", request=request)
        name = f"{slug}_{timezone.localdate()}.{fmt}"
        buf = exports.to_xlsx(rep, rtl) if fmt == "xlsx" else exports.to_pdf(rep, rtl)
        return FileResponse(buf, as_attachment=True, filename=name)
    return render(request, "reports/report.html", {
        "rep": rep, "f": f, "slug": slug, "statuses": Decision.Status.choices, "priorities": Priority.choices,
        "month": request.GET.get("month") or timezone.localdate().strftime("%Y-%m")})
