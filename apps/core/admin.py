from django.contrib import admin

from .models import AcademicYear, AuditLog, Notification, Semester, SystemSettings


class SemesterInline(admin.TabularInline):
    model = Semester
    extra = 1


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_current")
    inlines = [SemesterInline]


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    pass


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "message_en")
    list_filter = ("action",)
    search_fields = ("message_en", "message_ar")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


admin.site.register(Notification)
