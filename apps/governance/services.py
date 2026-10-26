"""Decision-to-Action engine.

Meeting → MOM approval → Decision registration → Task assignment → Progress →
Evidence → Verification → Decision closure → Archive.
All rules are enforced here, server-side; views only call these functions.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core import permissions as P
from apps.core.models import AcademicYear, Notification
from apps.core.services import audit, notify

from .models import (Decision, DecisionStatusHistory, Escalation, Evidence, ExtensionRequest, Task, TaskAssignment,
                     TaskUpdate, Verification)

K = Notification.Kind


def _year_tag(d=None):
    y = AcademicYear.for_date(d or timezone.localdate())
    if y and "-" in y.name:
        a, b = y.name.split("-")[:2]
        return a[-2:] + b[-2:]
    return timezone.localdate().strftime("%y")


def _next_code(model, prefix):
    n = model.objects.filter(code__startswith=prefix).count() + 1
    while model.objects.filter(code=f"{prefix}{n:03d}").exists():
        n += 1
    return f"{prefix}{n:03d}"


def _mgmt():
    from apps.accounts.models import User
    return list(User.objects.filter(is_active=True, global_role__in=["dean", "vice_dean"]))


# ------------------------------------------------------------------ decisions
def _set_decision_status(decision, to, actor, note=""):
    if decision.status == to:
        return
    DecisionStatusHistory.objects.create(decision=decision, from_status=decision.status, to_status=to,
                                         actor=actor, note=note)
    decision.status = to


@transaction.atomic
def register_decision(user, meeting, *, title, description="", category="academic", priority="medium",
                      is_actionable=True, responsible=None, target_date=None, required_evidence="", is_demo=False):
    P.require(P.can_register_decisions(user, meeting))
    c = meeting.committee
    code = _next_code(Decision, f"DEC-{_year_tag(meeting.date)}-{c.code.upper()[:8]}-")
    d = Decision(code=code, committee=c, meeting=meeting, title=title[:300], description=description,
                 category=category, priority=priority, is_actionable=is_actionable, responsible=responsible,
                 target_date=target_date, required_evidence=required_evidence, created_by=user, is_demo=is_demo,
                 status=Decision.Status.REGISTERED if is_actionable else Decision.Status.NOTED)
    d.save()
    DecisionStatusHistory.objects.create(decision=d, to_status=d.status, actor=user, note="registered")
    audit(user, "decision.register", f"Registered decision {code}: {title}", f"تسجيل القرار {code}: {title}", d, c)
    if responsible:
        notify([responsible], K.TASK_ASSIGNED, f"You are responsible for decision {code}",
               f"أنت مسؤول عن القرار {code}", reverse("governance:decision", args=[d.id]))
    return d


@transaction.atomic
def edit_decision(user, decision, **fields):
    P.require(P.can_manage_meetings(user, decision.committee))
    if not decision.is_open and decision.status != Decision.Status.NOTED:
        raise ValidationError(_("Closed decisions cannot be edited."))
    for k, v in fields.items():
        setattr(decision, k, v)
    decision.save()
    audit(user, "decision.edit", f"Edited decision {decision.code}", f"تعديل القرار {decision.code}", decision,
          decision.committee)


def recompute_decision(decision, actor=None):
    """A decision is implemented only when every non-cancelled task is verified."""
    if decision.status in (Decision.Status.NOTED, Decision.Status.CANCELLED, Decision.Status.SUPERSEDED):
        return
    tasks = [t for t in decision.tasks.all() if t.status != Task.Status.CANCELLED]
    if not tasks:
        new = Decision.Status.REGISTERED
    elif all(t.status == Task.Status.VERIFIED for t in tasks):
        new = Decision.Status.IMPLEMENTED
    elif any(t.status != Task.Status.NOT_STARTED or t.progress > 0 for t in tasks):
        new = Decision.Status.IN_PROGRESS
    else:
        new = Decision.Status.ASSIGNED
    if new == decision.status:
        return
    was_closed = decision.status == Decision.Status.IMPLEMENTED
    _set_decision_status(decision, new, actor, "all tasks verified" if new == "implemented" else "")
    decision.closure_date = timezone.now() if new == Decision.Status.IMPLEMENTED else None
    decision.save(update_fields=["status", "closure_date"])
    if new == Decision.Status.IMPLEMENTED and not was_closed:
        from apps.committees.services import archive_mom_if_complete
        recipients = [decision.responsible, decision.committee.chair] + _mgmt()
        notify(recipients, K.DECISION_CLOSED, f"Decision {decision.code} implemented and closed",
               f"تم تنفيذ القرار {decision.code} وإغلاقه", reverse("governance:decision", args=[decision.id]))
        audit(actor, "decision.closed", f"Decision {decision.code} closed: all tasks verified",
              f"إغلاق القرار {decision.code}: تم التحقق من جميع المهام", decision, decision.committee)
        archive_mom_if_complete(decision.meeting.mom, actor)


@transaction.atomic
def request_cancellation(user, decision, reason, superseded_by=None):
    P.require(P.can_manage_meetings(user, decision.committee) or P.is_exec(user))
    if not decision.is_open:
        raise ValidationError(_("Only open decisions can be cancelled."))
    if not reason.strip():
        raise ValidationError(_("A reason is required."))
    decision.cancel_requested = True
    decision.cancel_reason = reason
    decision.cancel_requested_by = user
    decision.superseded_by = superseded_by
    decision.save()
    notify(_mgmt(), K.ESCALATION, f"Cancellation requested for decision {decision.code}",
           f"طلب إلغاء القرار {decision.code}", reverse("governance:decision", args=[decision.id]))
    audit(user, "decision.cancel_request", f"Requested cancellation of {decision.code}: {reason}",
          f"طلب إلغاء القرار {decision.code}: {reason}", decision, decision.committee)


@transaction.atomic
def decide_cancellation(user, decision, approve, note=""):
    P.require(P.can_cancel_decision(user, decision))
    if not decision.cancel_requested:
        raise ValidationError(_("No cancellation was requested."))
    decision.cancel_requested = False
    if approve:
        to = Decision.Status.SUPERSEDED if decision.superseded_by_id else Decision.Status.CANCELLED
        _set_decision_status(decision, to, user, f"{decision.cancel_reason} | {note}")
        decision.cancel_approved_by = user
        decision.closure_date = timezone.now()
        for t in decision.tasks.filter(status__in=Task.OPEN):
            _task_status(t, Task.Status.CANCELLED, user, TaskUpdate.Kind.CANCELLED, _("Decision cancelled"))
            t.save()
    decision.save()
    audit(user, "decision.cancel_decided", f"{'Approved' if approve else 'Rejected'} cancellation of {decision.code}",
          f"{'اعتماد' if approve else 'رفض'} إلغاء القرار {decision.code}", decision, decision.committee)
    if approve:
        from apps.committees.services import archive_mom_if_complete
        archive_mom_if_complete(decision.meeting.mom, user)


# ------------------------------------------------------------------ tasks
def _task_status(task, to, actor, kind, note="", progress=None):
    TaskUpdate.objects.create(task=task, actor=actor, kind=kind, from_status=task.status, to_status=to,
                              progress=progress if progress is not None else task.progress, note=note)
    task.status = to


def _task_url(task):
    return reverse("governance:task", args=[task.id])


def _reviewers(task):
    if task.reviewer_id:
        return [task.reviewer]
    if task.source == Task.Source.MANAGEMENT:
        return [task.assigned_by] if task.assigned_by and task.assigned_by.is_executive else _mgmt()
    chair = task.committee.chair if task.committee_id else None
    return [chair] if chair else _mgmt()


@transaction.atomic
def create_task(user, *, title, assignees, source=Task.Source.COMMITTEE, committee=None, decision=None,
                plan_item=None, description="", category="academic", priority="medium", due_date=None,
                expected_deliverables="", reviewer=None, is_urgent=False, is_demo=False):
    if source == Task.Source.MANAGEMENT or is_urgent:
        P.require(P.can_assign_urgent(user))
    elif decision is not None:
        P.require(P.can_convert_decision(user, decision))
        if not decision.meeting.mom.is_approved:
            raise ValidationError(_("Tasks can be created only after the minutes are approved."))
        if not decision.is_open:
            raise ValidationError(_("This decision is closed."))
        committee = decision.committee
    else:
        P.require(committee is not None and P.can_create_committee_task(user, committee))
    if not assignees:
        raise ValidationError(_("Assign at least one responsible member."))
    prefix = "MGT" if source == Task.Source.MANAGEMENT else "TSK"
    code = _next_code(Task, f"{prefix}-{_year_tag()}-")
    t = Task.objects.create(code=code, source=source, committee=committee, decision=decision, plan_item=plan_item,
                            title=title[:300], description=description, category=category, priority=priority,
                            is_urgent=is_urgent or priority == "urgent" and source == Task.Source.MANAGEMENT,
                            due_date=due_date, expected_deliverables=expected_deliverables, reviewer=reviewer,
                            assigned_by=user, is_demo=is_demo)
    for i, u in enumerate(dict.fromkeys(assignees)):
        TaskAssignment.objects.create(task=t, user=u, is_primary=(i == 0), assigned_by=user)
    TaskUpdate.objects.create(task=t, actor=user, kind=TaskUpdate.Kind.CREATED, to_status=t.status, progress=0,
                              note=_("Task created"))
    if plan_item is not None:
        plan_item.status = "tasked"
        plan_item.save(update_fields=["status"])
    if decision is not None:
        recompute_decision(decision, user)
    kind = K.TASK_URGENT if t.is_urgent else K.TASK_ASSIGNED
    tag_en, tag_ar = ("URGENT task" if t.is_urgent else "New task"), ("مهمة عاجلة" if t.is_urgent else "مهمة جديدة")
    notify(list(assignees), kind, f"{tag_en} {code}: {title}", f"{tag_ar} {code}: {title}", _task_url(t))
    audit(user, "task.create", f"Created task {code}: {title}", f"إنشاء المهمة {code}: {title}", t, committee)
    return t


def convert_decision(user, decision, specs):
    """specs: list of dicts with title, description, assignees, due_date, priority, expected_deliverables, reviewer."""
    if not specs:
        raise ValidationError(_("Add at least one task."))
    tasks = []
    with transaction.atomic():
        for s in specs:
            tasks.append(create_task(user, source=Task.Source.DECISION, decision=decision, category=decision.category,
                                     is_demo=decision.is_demo, **s))
    return tasks


@transaction.atomic
def update_progress(user, task, *, progress, note="", blocked=False):
    P.require(P.can_update_task(user, task))
    if task.status in (Task.Status.SUBMITTED,):
        raise ValidationError(_("The task is awaiting verification."))
    progress = max(0, min(100, int(progress)))
    to = Task.Status.BLOCKED if blocked else Task.Status.IN_PROGRESS
    kind = TaskUpdate.Kind.OBSTACLE if blocked else TaskUpdate.Kind.PROGRESS
    _task_status(task, to, user, kind, note, progress)
    task.progress = progress
    task.save(update_fields=["status", "progress"])
    if task.decision_id:
        recompute_decision(task.decision, user)
    if blocked:
        notify([task.committee.chair if task.committee_id else None] + ([task.assigned_by] if task.source == "management" else []),
               K.ESCALATION, f"Obstacle reported on {task.code}", f"تم الإبلاغ عن عائق في المهمة {task.code}",
               _task_url(task))
    audit(user, "task.progress", f"{task.code}: {to} {progress}% {note}", f"{task.code}: {progress}% {note}",
          task, task.committee)


@transaction.atomic
def add_evidence(user, task, uploaded_file, description=""):
    from apps.documents.models import Document
    from apps.documents.services import store_file
    P.require(P.can_update_task(user, task))
    links = {"task": task, "committee": task.committee}
    if task.decision_id:
        links.update(decision=task.decision, meeting=task.decision.meeting)
    doc, _v = store_file(uploaded_file, user, title=description or f"Evidence {task.code}",
                         category=Document.Category.EVIDENCE, **links)
    rnd = task.verifications.filter(outcome=Verification.Outcome.RETURNED).count() + 1
    ev = Evidence.objects.create(task=task, document=doc, description=description[:300], submitted_by=user,
                                 review_round=rnd)
    TaskUpdate.objects.create(task=task, actor=user, kind=TaskUpdate.Kind.EVIDENCE, from_status=task.status,
                              to_status=task.status, note=f"{description} ({_v.original_name})")
    audit(user, "task.evidence", f"Evidence uploaded to {task.code}: {_v.original_name}",
          f"رفع شاهد للمهمة {task.code}: {_v.original_name}", task, task.committee)
    return ev


@transaction.atomic
def submit_for_verification(user, task, note=""):
    P.require(P.can_update_task(user, task))
    if task.status not in Task.WORKING:
        raise ValidationError(_("This task cannot be submitted now."))
    rnd = task.verifications.filter(outcome=Verification.Outcome.RETURNED).count() + 1
    if not task.evidence.filter(review_round=rnd).exists():
        raise ValidationError(_("Upload at least one evidence file before submitting for verification."))
    _task_status(task, Task.Status.SUBMITTED, user, TaskUpdate.Kind.SUBMITTED, note, 100)
    task.progress = 100
    task.submitted_at = timezone.now()
    task.save(update_fields=["status", "progress", "submitted_at"])
    if task.decision_id:
        recompute_decision(task.decision, user)
    notify(_reviewers(task), K.VERIFY_REQUEST, f"{task.code} submitted for verification: {task.title}",
           f"المهمة {task.code} مقدمة للتحقق: {task.title}", _task_url(task), dedupe_key=f"verify-{task.id}-{rnd}")
    audit(user, "task.submit", f"{task.code} submitted for verification", f"تقديم المهمة {task.code} للتحقق",
          task, task.committee)


@transaction.atomic
def verify_task(user, task, approve, comment=""):
    P.require(P.can_verify_task(user, task))
    rnd = task.verifications.filter(outcome=Verification.Outcome.RETURNED).count() + 1
    if approve:
        Verification.objects.create(task=task, reviewer=user, outcome=Verification.Outcome.APPROVED,
                                    comment=comment, review_round=rnd)
        _task_status(task, Task.Status.VERIFIED, user, TaskUpdate.Kind.VERIFIED, comment, 100)
        task.verified_at = timezone.now()
        task.verified_by = user
        task.save(update_fields=["status", "verified_at", "verified_by"])
        task.documents.update(is_locked=True)
        notify(task.active_assignees(), K.TASK_VERIFIED, f"{task.code} verified and closed",
               f"تم التحقق من المهمة {task.code} وإغلاقها", _task_url(task))
    else:
        if not comment.strip():
            raise ValidationError(_("Explain what is missing before returning the task."))
        Verification.objects.create(task=task, reviewer=user, outcome=Verification.Outcome.RETURNED,
                                    comment=comment, review_round=rnd)
        _task_status(task, Task.Status.RETURNED, user, TaskUpdate.Kind.RETURNED, comment, task.progress)
        task.save(update_fields=["status"])
        notify(task.active_assignees(), K.TASK_RETURNED, f"{task.code} returned for more work",
               f"أعيدت المهمة {task.code} لاستكمال العمل", _task_url(task), dedupe_key=f"ret-{task.id}-{rnd}")
    if task.decision_id:
        recompute_decision(task.decision, user)
    audit(user, "task.verify" if approve else "task.return",
          f"{task.code} {'verified and closed' if approve else 'returned'}: {comment}",
          f"{task.code} {'تم التحقق والإغلاق' if approve else 'أعيدت'}: {comment}", task, task.committee)


@transaction.atomic
def request_extension(user, task, new_due, reason):
    P.require(P.can_update_task(user, task))
    if task.extension_requests.filter(status=ExtensionRequest.Status.PENDING).exists():
        raise ValidationError(_("An extension request is already pending."))
    if not reason.strip():
        raise ValidationError(_("A reason is required."))
    r = ExtensionRequest.objects.create(task=task, requested_by=user, current_due=task.due_date,
                                        requested_due=new_due, reason=reason)
    TaskUpdate.objects.create(task=task, actor=user, kind=TaskUpdate.Kind.EXTENSION, from_status=task.status,
                              to_status=task.status, note=f"→ {new_due}: {reason}")
    chair = task.committee.chair if task.committee_id else None
    managers = _mgmt() if (task.source == "management" or chair is None) else [chair]
    notify(managers, K.EXTENSION, f"Extension requested for {task.code} to {new_due}",
           f"طلب مد مهلة المهمة {task.code} إلى {new_due}", _task_url(task))
    return r


@transaction.atomic
def decide_extension(user, req, approve, note=""):
    task = req.task
    P.require(P.can_manage_task(user, task))
    req.status = ExtensionRequest.Status.APPROVED if approve else ExtensionRequest.Status.REJECTED
    req.decided_by, req.decided_at, req.decision_note = user, timezone.now(), note
    req.save()
    if approve:
        task.due_date = req.requested_due
        task.save(update_fields=["due_date"])
    TaskUpdate.objects.create(task=task, actor=user, kind=TaskUpdate.Kind.EXTENSION, from_status=task.status,
                              to_status=task.status,
                              note=f"{'approved' if approve else 'rejected'}: {req.requested_due} {note}")
    notify([req.requested_by], K.EXTENSION,
           f"Extension {'approved' if approve else 'rejected'} for {task.code}",
           f"{'تمت الموافقة على' if approve else 'رُفض'} مد مهلة {task.code}", _task_url(task))
    audit(user, "task.extension", f"Extension for {task.code} {'approved' if approve else 'rejected'}",
          f"{'اعتماد' if approve else 'رفض'} مد مهلة {task.code}", task, task.committee)


@transaction.atomic
def escalate(user, task, reason):
    P.require(P.can_update_task(user, task) or P.is_exec(user))
    if not reason.strip():
        raise ValidationError(_("Describe what is preventing completion."))
    e = Escalation.objects.create(task=task, decision=task.decision, committee=task.committee, raised_by=user,
                                  reason=reason)
    TaskUpdate.objects.create(task=task, actor=user, kind=TaskUpdate.Kind.ESCALATION, from_status=task.status,
                              to_status=task.status, note=reason)
    notify([u for u in _mgmt() if u != user], K.ESCALATION, f"Escalation on {task.code}: {reason[:120]}",
           f"تصعيد على المهمة {task.code}: {reason[:120]}", _task_url(task))
    audit(user, "task.escalate", f"Escalated {task.code}: {reason}", f"تصعيد المهمة {task.code}: {reason}", task,
          task.committee)
    return e


@transaction.atomic
def resolve_escalation(user, esc, resolution):
    P.require(P.is_exec(user))
    esc.status, esc.resolution, esc.resolved_by, esc.resolved_at = (Escalation.Status.RESOLVED, resolution, user,
                                                                    timezone.now())
    esc.save()
    if esc.task_id:
        TaskUpdate.objects.create(task=esc.task, actor=user, kind=TaskUpdate.Kind.ESCALATION,
                                  from_status=esc.task.status, to_status=esc.task.status,
                                  note=f"resolved: {resolution}")
        notify([esc.raised_by] + esc.task.active_assignees(), K.COMMENT, f"Escalation resolved on {esc.task.code}",
               f"تم حل التصعيد على المهمة {esc.task.code}", _task_url(esc.task))
    audit(user, "escalation.resolve", f"Resolved escalation #{esc.id}: {resolution}",
          f"حل التصعيد رقم {esc.id}: {resolution}", esc, esc.committee)


@transaction.atomic
def reassign(user, task, new_assignees, note=""):
    P.require(P.can_manage_task(user, task))
    if task.status in ("verified", "cancelled"):
        raise ValidationError(_("Closed tasks cannot be reassigned."))
    if not new_assignees:
        raise ValidationError(_("Assign at least one responsible member."))
    now = timezone.now()
    task.assignments.filter(active=True).update(active=False, ended_at=now)
    for i, u in enumerate(dict.fromkeys(new_assignees)):
        TaskAssignment.objects.create(task=task, user=u, is_primary=(i == 0), assigned_by=user)
    names = ", ".join(u.username for u in new_assignees)
    TaskUpdate.objects.create(task=task, actor=user, kind=TaskUpdate.Kind.REASSIGNED, from_status=task.status,
                              to_status=task.status, note=f"{names} {note}")
    notify(list(new_assignees), K.TASK_URGENT if task.is_urgent else K.TASK_ASSIGNED,
           f"Task {task.code} assigned to you: {task.title}", f"أسندت إليك المهمة {task.code}: {task.title}",
           _task_url(task))
    audit(user, "task.reassign", f"Reassigned {task.code} to {names}", f"إعادة إسناد المهمة {task.code}", task,
          task.committee)


@transaction.atomic
def edit_task(user, task, **fields):
    P.require(P.can_manage_task(user, task))
    if task.status in ("verified", "cancelled"):
        raise ValidationError(_("Closed tasks cannot be edited."))
    changed = []
    for k, v in fields.items():
        if getattr(task, k) != v:
            changed.append(f"{k}: {getattr(task, k)} → {v}")
            setattr(task, k, v)
    if changed:
        task.save()
        TaskUpdate.objects.create(task=task, actor=user, kind=TaskUpdate.Kind.EDITED, from_status=task.status,
                                  to_status=task.status, note="; ".join(changed))
        audit(user, "task.edit", f"Edited {task.code}: {'; '.join(changed)}", f"تعديل المهمة {task.code}", task,
              task.committee)


@transaction.atomic
def cancel_task(user, task, reason):
    P.require(P.can_manage_task(user, task))
    if not reason.strip():
        raise ValidationError(_("A reason is required."))
    _task_status(task, Task.Status.CANCELLED, user, TaskUpdate.Kind.CANCELLED, reason)
    task.save(update_fields=["status"])
    if task.decision_id:
        recompute_decision(task.decision, user)
    audit(user, "task.cancel", f"Cancelled {task.code}: {reason}", f"إلغاء المهمة {task.code}: {reason}", task,
          task.committee)


@transaction.atomic
def add_comment(user, body, *, task=None, decision=None, meeting=None):
    from apps.core.models import Comment
    if task is not None:
        P.require(P.can_view_task(user, task))
    committee = (task.committee if task else decision.committee if decision else meeting.committee if meeting else None)
    if committee is not None and task is None:
        P.require(P.can_view_committee(user, committee))
    c = Comment.objects.create(author=user, body=body, task=task, decision=decision, meeting=meeting,
                               is_management=P.is_exec(user))
    if task is not None:
        TaskUpdate.objects.create(task=task, actor=user, kind=TaskUpdate.Kind.COMMENT, from_status=task.status,
                                  to_status=task.status, note=body)
    if c.is_management:
        recipients = (task.active_assignees() if task else []) + ([committee.chair] if committee else [])
        target = task.code if task else decision.code if decision else meeting.label
        url = (_task_url(task) if task else reverse("governance:decision", args=[decision.id]) if decision
               else reverse("committees:meeting", args=[meeting.id]))
        notify([u for u in recipients if u != user], K.COMMENT, f"Management comment on {target}",
               f"تعليق من الإدارة على {target}", url)
    audit(user, "comment", f"Comment: {body[:200]}", f"تعليق: {body[:200]}", task or decision or meeting, committee)
    return c
