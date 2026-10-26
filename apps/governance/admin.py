from django.contrib import admin

from .models import Decision, Escalation, Evidence, ExtensionRequest, Task, TaskAssignment, TaskUpdate, Verification


@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    list_display = ("code", "committee", "title", "status", "target_date", "is_demo")
    list_filter = ("committee", "status", "is_demo")
    search_fields = ("code", "title")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "committee", "status", "due_date", "is_demo")
    list_filter = ("status", "source", "is_demo")
    search_fields = ("code", "title")


for m in (TaskAssignment, TaskUpdate, Evidence, Verification, ExtensionRequest, Escalation):
    admin.site.register(m)
