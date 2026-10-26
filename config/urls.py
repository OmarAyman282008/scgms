from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("accounts/", include("apps.accounts.urls")),
    path("committees/", include("apps.committees.urls")),
    path("", include("apps.governance.urls")),
    path("archive/", include("apps.documents.urls")),
    path("reports/", include("apps.reports.urls")),
    path("", include("apps.core.urls")),
]
