from django.contrib import admin

from .models import Document, DocumentTemplate, DocumentVersion


class VersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    readonly_fields = ("version_no", "original_name", "size", "sha256", "uploaded_by", "uploaded_at")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "committee", "is_locked", "is_demo")
    list_filter = ("category", "committee", "is_demo")
    inlines = [VersionInline]


admin.site.register(DocumentTemplate)
