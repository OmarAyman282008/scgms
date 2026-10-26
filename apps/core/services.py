"""Audit trail and notifications, used by every workflow service."""
import logging
import threading
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import AuditLog, Notification, SystemSettings

log = logging.getLogger(__name__)
_state = threading.local()


@contextmanager
def silence():
    """Suppress notifications (used when loading demonstration data)."""
    _state.silent = True
    try:
        yield
    finally:
        _state.silent = False


def audit(actor, action, message_en, message_ar, obj=None, committee=None, request=None):
    ip = None
    if request is not None:
        ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
              or request.META.get("REMOTE_ADDR"))
    return AuditLog.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None, action=action,
        object_type=obj.__class__.__name__.lower() if obj is not None else "",
        object_id=getattr(obj, "pk", None), committee=committee,
        message_en=message_en[:500], message_ar=message_ar[:500], ip_address=ip or None)


def notify(recipients, kind, title_en, title_ar, url="", dedupe_key="", dedupe_hours=20):
    """Create in-app notifications (and queue e-mail when enabled).
    The same dedupe_key is not repeated to one person within `dedupe_hours`,
    so daily reminders never pile up."""
    if getattr(_state, "silent", False):
        return []
    seen = set()
    created = []
    since = timezone.now() - timedelta(hours=dedupe_hours)
    for user in recipients:
        if user is None or not user.is_active or user.id in seen:
            continue
        seen.add(user.id)
        if dedupe_key and Notification.objects.filter(recipient=user, dedupe_key=dedupe_key,
                                                      created_at__gte=since).exists():
            continue
        created.append(Notification.objects.create(recipient=user, kind=kind, title_en=title_en[:300],
                                                   title_ar=title_ar[:300], url=url, dedupe_key=dedupe_key))
    if created and SystemSettings.get().email_notifications_enabled:
        from .tasks import send_notification_emails
        ids = [n.id for n in created if not n.recipient.daily_digest_only]
        if ids:
            try:
                send_notification_emails.delay(ids)
            except Exception:  # broker down: never block the user's action
                log.exception("could not queue e-mail notifications")
    return created


def email_notifications(ids):
    for n in Notification.objects.filter(id__in=ids, emailed=False).select_related("recipient"):
        u = n.recipient
        if not (u.email and u.email_notifications):
            continue
        link = f"{settings.SITE_URL}{n.url}" if n.url else settings.SITE_URL
        body = f"{n.title_ar}\n{n.title_en}\n\n{link}\n\n— SCGMS · {settings.SCHOOL_NAME_EN}"
        try:
            send_mail(f"[SCGMS] {n.title_en}", body, settings.DEFAULT_FROM_EMAIL, [u.email])
            n.emailed = True
            n.save(update_fields=["emailed"])
        except Exception:
            log.exception("e-mail to %s failed", u.email)
