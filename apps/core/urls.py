from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.home, name="home"),
    path("overview/", views.overview, name="overview"),
    path("me/", views.me, name="me"),
    path("follow-up/", views.followup, name="followup"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("calendar/events.json", views.calendar_events, name="calendar_events"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/<int:pk>/", views.notification_open, name="notification_open"),
    path("notifications/read-all/", views.notifications_read_all, name="notifications_read_all"),
    path("notifications/badge/", views.notification_badge, name="notification_badge"),
    path("administration/", views.admin_home, name="admin_home"),
    path("administration/settings/", views.settings_view, name="settings"),
    path("administration/audit/", views.audit_log, name="audit"),
]
