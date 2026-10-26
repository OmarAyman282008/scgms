from django import template
from django.utils.html import format_html
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from apps.committees.models import MOM, Meeting, Membership
from apps.governance.models import Decision, Priority, Task

register = template.Library()

COLORS = {
    "task": {"not_started": "secondary", "in_progress": "primary", "blocked": "warning", "submitted": "info",
             "returned": "warning", "verified": "success", "cancelled": "light", "overdue": "danger"},
    "decision": {"registered": "secondary", "assigned": "info", "in_progress": "primary", "implemented": "success",
                 "noted": "light", "cancelled": "light", "superseded": "light", "overdue": "danger"},
    "mom": {"draft": "secondary", "submitted": "info", "under_review": "primary", "revision": "warning",
            "approved": "success", "archived": "dark"},
    "meeting": {"scheduled": "primary", "held": "success", "cancelled": "secondary"},
    "priority": {"low": "secondary", "medium": "info", "high": "warning", "urgent": "danger"},
    "health": {"good": "success", "warning": "warning", "critical": "danger"},
}
LABELS = {
    "task": dict(Task.Status.choices), "decision": dict(Decision.Status.choices), "mom": dict(MOM.Status.choices),
    "meeting": dict(Meeting.Status.choices), "priority": dict(Priority.choices),
}
ICONS = {"success": "check-circle-fill", "danger": "exclamation-octagon-fill", "warning": "exclamation-triangle-fill",
         "info": "hourglass-split", "primary": "arrow-repeat", "secondary": "circle", "light": "dash-circle",
         "dark": "archive-fill"}


def _label(kind, value):
    if value == "overdue":
        return _("Overdue")
    if kind == "health":
        return {"good": _("On track"), "warning": _("Needs attention"), "critical": _("Intervention needed")}[value]
    return LABELS.get(kind, {}).get(value, value)


@register.simple_tag
def badge(kind, value):
    color = COLORS.get(kind, {}).get(value, "secondary")
    txt = "text-dark" if color in ("warning", "light", "info") else ""
    icon = "flag-fill" if kind == "priority" else ICONS.get(color, "circle")
    return format_html('<span class="badge text-bg-{} {} st-badge"><i class="bi bi-{}"></i> {}</span>', color, txt,
                       icon, _label(kind, value))


@register.simple_tag
def task_badge(t):
    return badge("task", t.display_status)


@register.simple_tag
def decision_badge(d):
    return badge("decision", "overdue" if d.is_overdue else d.status)


@register.filter
def pct(v):
    return "—" if v is None else f"{v}%"


@register.filter
def role_label(v):
    return dict(Membership.Role.choices).get(v, v)


@register.filter
def get(d, k):
    try:
        return d.get(k)
    except AttributeError:
        return None


@register.filter
def pick(obj, field):
    """{{ obj|pick:'name' }} → obj.name_en / obj.name_ar by language."""
    en = (get_language() or "ar").startswith("en")
    return getattr(obj, f"{field}_en" if en else f"{field}_ar", "") or getattr(obj, f"{field}_ar", "")


@register.simple_tag(takes_context=True)
def qs(context, **kw):
    g = context["request"].GET.copy()
    for k, v in kw.items():
        if v in (None, ""):
            g.pop(k, None)
        else:
            g[k] = v
    return "?" + g.urlencode()


@register.filter
def initials(user):
    name = getattr(user, "display_name", str(user))
    parts = [p for p in name.replace("/", " ").split() if p not in ("أ.د", "د", "م", "Prof.", "Dr.", "Eng.")]
    return "".join(p[0] for p in parts[:2]).upper() if parts else "?"


@register.filter
def health_color(h):
    return COLORS["health"].get(h, "secondary")


@register.filter
def weekday(i):
    names = [_("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"), _("Friday"), _("Saturday"), _("Sunday")]
    try:
        return names[int(i)]
    except (TypeError, ValueError, IndexError):
        return ""


@register.filter
def task_status_label(v):
    return LABELS["task"].get(v, v) if v else "—"
