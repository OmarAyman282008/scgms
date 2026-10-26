from django.core.management.base import BaseCommand
from django.db import transaction

from apps.committees.models import ActionPlanItem, Meeting
from apps.core.models import AuditLog, Notification
from apps.documents.models import Document
from apps.governance.models import Decision, Evidence, Task


class Command(BaseCommand):
    help = "Remove all DEMO meetings, minutes, decisions, tasks and their files. Real data is untouched."

    @transaction.atomic
    def handle(self, *args, **opts):
        tasks = Task.objects.filter(is_demo=True)
        Evidence.objects.filter(task__in=tasks).delete()
        docs = Document.objects.filter(is_demo=True)
        for d in docs:
            for v in d.versions.all():
                v.file.delete(save=False)
        from apps.committees.models import MOM
        MOM.objects.filter(document__in=docs).update(document=None)
        n_docs = docs.count()
        docs.delete()
        n_tasks = tasks.count()
        tasks.delete()
        n_dec = Decision.objects.filter(is_demo=True).count()
        Decision.objects.filter(is_demo=True).delete()
        n_meet = Meeting.objects.filter(is_demo=True).count()
        Meeting.objects.filter(is_demo=True).delete()
        ActionPlanItem.objects.filter(is_demo=True).delete()
        ActionPlanItem.objects.filter(status="tasked", tasks__isnull=True).update(status="planned")
        AuditLog.objects.filter(message_en__startswith="[DEMO]").delete()
        Notification.objects.filter(title_en__startswith="[DEMO]").delete()
        self.stdout.write(self.style.SUCCESS(
            f"Removed {n_meet} meetings, {n_dec} decisions, {n_tasks} tasks and {n_docs} documents (DEMO)."))
