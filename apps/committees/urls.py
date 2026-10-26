from django.urls import path

from . import views

app_name = "committees"
urlpatterns = [
    path("", views.committee_list, name="list"),
    path("new/", views.committee_form, name="new"),
    path("<int:pk>/", views.committee_detail, name="detail"),
    path("<int:pk>/edit/", views.committee_form, name="edit"),
    path("<int:pk>/members/", views.member_set, name="member_set"),
    path("<int:pk>/members/<int:mid>/end/", views.member_end, name="member_end"),
    path("<int:pk>/responsibilities/", views.responsibility_save, name="responsibility_save"),
    path("<int:pk>/plan/", views.plan_save, name="plan_save"),
    path("<int:pk>/generate-meetings/", views.generate_meetings, name="generate_meetings"),
    path("meetings/", views.meeting_list, name="meetings"),
    path("meetings/new/", views.meeting_new, name="meeting_new"),
    path("meetings/<int:pk>/", views.meeting_detail, name="meeting"),
    path("meetings/<int:pk>/edit/", views.meeting_edit, name="meeting_edit"),
    path("meetings/<int:pk>/action/", views.meeting_action, name="meeting_action"),
    path("meetings/<int:pk>/mom-template.docx", views.mom_template, name="mom_template"),
    path("moms/", views.mom_list, name="moms"),
]
