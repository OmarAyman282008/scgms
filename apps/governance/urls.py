from django.urls import path

from . import views

app_name = "governance"
urlpatterns = [
    path("decisions/", views.decision_list, name="decisions"),
    path("decisions/new/<int:meeting_id>/", views.decision_new, name="decision_new"),
    path("decisions/<int:pk>/", views.decision_detail, name="decision"),
    path("decisions/<int:pk>/action/", views.decision_action, name="decision_action"),
    path("decisions/<int:pk>/convert/", views.decision_convert, name="decision_convert"),
    path("tasks/", views.task_list, name="tasks"),
    path("tasks/new/", views.task_new, name="task_new"),
    path("tasks/<int:pk>/", views.task_detail, name="task"),
    path("tasks/<int:pk>/action/", views.task_action, name="task_action"),
]
