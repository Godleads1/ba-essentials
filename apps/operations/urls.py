from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="staff-dashboard"),
    path("queue/", views.queue, name="staff-queue"),
    path("needs-information/", views.needs_information, name="staff-needs-information"),
    path("scoping/", views.scoping_queue, name="staff-scoping"),
    path("assignments/", views.workload, name="staff-workload"),
    path("deliverables/", views.deliverables_queue, name="staff-deliverables"),
    path("my-cases/", views.my_cases, name="staff-my-cases"),
    path("audit/", views.audit_log, name="staff-audit"),
    path("clients/", views.clients, name="staff-clients"),
    path("tasks/<uuid:task_id>/", views.task_detail, name="staff-task-detail"),
    path("tasks/<uuid:task_id>/triage/", views.triage, name="staff-task-triage"),
    path(
        "tasks/<uuid:task_id>/request-information/",
        views.request_information,
        name="staff-task-request-info",
    ),
    path(
        "tasks/<uuid:task_id>/scoping/", views.begin_scoping, name="staff-task-scope"
    ),
    path("tasks/<uuid:task_id>/assign/", views.assign, name="staff-task-assign"),
    path(
        "tasks/<uuid:task_id>/assignment/accept/",
        views.accept_assignment,
        name="staff-assignment-accept",
    ),
    path(
        "tasks/<uuid:task_id>/work-log/",
        views.add_work_log,
        name="staff-task-worklog",
    ),
    path(
        "tasks/<uuid:task_id>/deliverable/",
        views.submit_deliverable,
        name="staff-deliverable-submit",
    ),
    path(
        "tasks/<uuid:task_id>/resume-work/",
        views.resume_work,
        name="staff-resume-work",
    ),
    path(
        "tasks/<uuid:task_id>/start-revision/",
        views.start_revision,
        name="staff-start-revision",
    ),
    path("tasks/<uuid:task_id>/hold/", views.hold, name="staff-task-hold"),
    path("tasks/<uuid:task_id>/resume/", views.resume, name="staff-task-resume"),
    path("tasks/<uuid:task_id>/cancel/", views.cancel, name="staff-task-cancel"),
]
