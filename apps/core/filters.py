"""Shared filter bar: academic year, semester, committee, date range, priority, person, status."""
from datetime import date

from django.utils import timezone

from apps.committees.models import Committee
from apps.core import permissions as P

from .models import AcademicYear, Semester


def _d(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


class Filters:
    def __init__(self, request):
        g = request.GET
        self.request = request
        self.years = list(AcademicYear.objects.all())
        self.year = AcademicYear.objects.filter(pk=g.get("year")).first() if g.get("year") else AcademicYear.current()
        self.semesters = list(Semester.objects.filter(academic_year=self.year)) if self.year else []
        self.semester = next((s for s in self.semesters if str(s.pk) == g.get("semester")), None)
        visible = P.committee_ids_for(request.user)
        self.all_committees = list(Committee.objects.filter(id__in=visible, is_active=True))
        self.committee = next((c for c in self.all_committees if str(c.pk) == g.get("committee")), None)
        self.priority = g.get("priority", "")
        self.person = g.get("person", "")
        self.status = g.get("status", "")
        today = timezone.localdate()
        start = self.semester.start_date if self.semester else (self.year.start_date if self.year else date(today.year, 1, 1))
        end = self.semester.end_date if self.semester else (self.year.end_date if self.year else date(today.year, 12, 31))
        self.start = _d(g.get("from")) or start
        self.end = _d(g.get("to")) or end
        self.raw = g

    @property
    def committees(self):
        return [self.committee] if self.committee else self.all_committees

    def task_q(self, prefix=""):
        q = {}
        if self.priority:
            q[f"{prefix}priority"] = self.priority
        if self.person:
            q[f"{prefix}assignments__user_id"] = self.person
            q[f"{prefix}assignments__active"] = True
        if self.status:
            q[f"{prefix}status"] = self.status
        return q

    def querystring(self, **override):
        g = self.raw.copy()
        for k, v in override.items():
            g[k] = v
        return g.urlencode()
