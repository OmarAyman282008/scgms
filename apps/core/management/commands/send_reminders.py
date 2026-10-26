from django.core.management.base import BaseCommand

from apps.core.reminders import run_daily_reminders


class Command(BaseCommand):
    help = "Send the daily meeting / MOM / task / decision reminders (safe to run more than once a day)."

    def handle(self, *args, **opts):
        self.stdout.write(str(run_daily_reminders()))
