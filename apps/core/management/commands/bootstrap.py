from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.committees.models import Committee


class Command(BaseCommand):
    help = "First-start setup used by Docker: migrate, load the school data if empty, optionally demo data."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true")

    def handle(self, *args, **opts):
        call_command("migrate", interactive=False, verbosity=0)
        if not Committee.objects.exists():
            call_command("seed_school")
            if opts["demo"]:
                call_command("seed_demo")
        self.stdout.write("bootstrap done")
