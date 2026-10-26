from django.contrib import admin

from .models import (MOM, ActionPlanItem, AgendaItem, Attendance, Committee, CommitteeResponsibility, Meeting,
                     Membership, MOMReview)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


class ResponsibilityInline(admin.TabularInline):
    model = CommitteeResponsibility
    extra = 0


@admin.register(Committee)
class CommitteeAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "name_en", "is_active")
    inlines = [ResponsibilityInline, MembershipInline]


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ("committee", "number", "date", "status", "is_demo")
    list_filter = ("committee", "status", "is_demo")


for m in (MOM, MOMReview, AgendaItem, Attendance, ActionPlanItem):
    admin.site.register(m)
