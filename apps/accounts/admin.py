from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class SCGMSUserAdmin(UserAdmin):
    list_display = ("username", "name_ar", "name_en", "global_role", "is_active")
    list_filter = ("global_role", "is_active")
    fieldsets = UserAdmin.fieldsets + (("SCGMS", {"fields": ("global_role", "name_ar", "name_en", "position",
                                                               "must_change_password", "email_notifications")}),)
