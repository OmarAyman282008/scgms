"""Server-side role-based access rules. Every view and workflow service calls these;
templates only use them to hide buttons, never as the enforcement point."""
from django.core.exceptions import PermissionDenied

CHAIR, SECRETARY, MEMBER = "chair", "secretary", "member"


def is_exec(user):
    return bool(user and user.is_authenticated and user.is_executive)


def is_admin(user):
    return bool(user and user.is_authenticated and user.is_sysadmin)


def sees_everything(user):
    return is_exec(user) or is_admin(user)


def roles_in(user, committee):
    """Set of active roles the user holds in the committee."""
    if not user.is_authenticated or committee is None:
        return set()
    cache = getattr(user, "_role_cache", None)
    if cache is None:
        cache = {}
        for m in user.memberships.filter(end_date__isnull=True).only("committee_id", "role"):
            cache.setdefault(m.committee_id, set()).add(m.role)
        user._role_cache = cache
    return cache.get(committee.id if hasattr(committee, "id") else committee, set())


def committee_ids_for(user):
    from apps.committees.models import Committee
    if sees_everything(user):
        return list(Committee.objects.values_list("id", flat=True))
    return list(user.memberships.filter(end_date__isnull=True).values_list("committee_id", flat=True).distinct())


def can_view_committee(user, committee):
    return sees_everything(user) or bool(roles_in(user, committee))


def is_chair(user, committee):
    return CHAIR in roles_in(user, committee)


def can_manage_committee(user, committee=None):
    """Create committees, change membership and responsibilities."""
    return is_admin(user) or is_exec(user)


def can_manage_meetings(user, committee):
    """Schedule meetings, agendas, attendance, upload MOMs."""
    return is_exec(user) or bool(roles_in(user, committee) & {CHAIR, SECRETARY})


def can_chair_review_mom(user, committee):
    """The chair reviews first; if the committee has no chair, management does it."""
    if is_chair(user, committee):
        return True
    return is_exec(user) and committee.chair is None


def can_management_approve_mom(user):
    return is_exec(user)


def can_register_decisions(user, meeting):
    mom = getattr(meeting, "mom", None)
    return bool(mom and mom.is_approved) and can_manage_meetings(user, meeting.committee)


def can_convert_decision(user, decision):
    return is_exec(user) or is_chair(user, decision.committee)


def can_cancel_decision(user, decision):
    """Formal cancellation / supersession is approved by management only."""
    return is_exec(user)


def can_view_task(user, task):
    if sees_everything(user):
        return True
    if any(a.user_id == user.id for a in task.assignments.all()):
        return True
    return task.committee_id is not None and bool(roles_in(user, task.committee))


def is_assignee(user, task):
    return task.assignments.filter(active=True, user=user).exists()


def can_update_task(user, task):
    if task.status in ("verified", "cancelled"):
        return False
    if is_assignee(user, task):
        return True
    return is_exec(user) or (task.committee_id and bool(roles_in(user, task.committee) & {CHAIR, SECRETARY}))


def can_verify_task(user, task):
    """Chair verifies committee work; management verifies its own assignments and can
    verify anything. Nobody verifies a task they are themselves assigned to."""
    if task.status != "submitted" or is_assignee(user, task):
        return False
    if task.reviewer_id:
        return user.id == task.reviewer_id or is_exec(user)
    if task.source == "management":
        return is_exec(user)
    return is_exec(user) or (task.committee_id and is_chair(user, task.committee))


def can_manage_task(user, task):
    """Reassign, change deadline, decide extensions, cancel."""
    if task.source == "management":
        return is_exec(user)
    return is_exec(user) or (task.committee_id and is_chair(user, task.committee))


def can_create_committee_task(user, committee):
    return is_exec(user) or bool(roles_in(user, committee) & {CHAIR, SECRETARY})


def can_assign_urgent(user):
    return is_exec(user)


def can_view_document(user, doc):
    if sees_everything(user):
        return True
    if doc.is_confidential:
        return False
    if doc.task_id and can_view_task(user, doc.task):
        return True
    if doc.committee_id:
        return can_view_committee(user, doc.committee)
    return False


def require(condition):
    if not condition:
        raise PermissionDenied
