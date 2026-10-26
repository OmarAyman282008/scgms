from celery import shared_task

from .services import email_notifications


@shared_task
def send_notification_emails(ids):
    email_notifications(ids)


@shared_task
def daily_reminders():
    from .reminders import run_daily_reminders
    return run_daily_reminders()
