from django.urls import path

from . import views

app_name = "documents"
urlpatterns = [
    path("", views.archive, name="archive"),
    path("documents/<int:pk>/", views.document_detail, name="detail"),
    path("documents/<int:pk>/version/", views.new_version, name="new_version"),
    path("file/<int:vid>/", views.download, name="download"),
    path("upload/", views.upload, name="upload"),
    path("committee/<int:pk>/export.zip", views.export_committee, name="export"),
    path("templates/", views.templates_view, name="templates"),
    path("templates/default.docx", views.template_download, name="template_default"),
    path("templates/<int:pk>.docx", views.template_download, name="template_download"),
]
