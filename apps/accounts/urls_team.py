from django.urls import path

from . import views_team as views

urlpatterns = [
    path("team/", views.team_home, name="staff-team"),
    path("team/invite/", views.team_invite, name="staff-team-invite"),
    path("team/requests/<uuid:request_id>/approve/", views.team_approve, name="staff-team-approve"),
    path("team/requests/<uuid:request_id>/reject/", views.team_reject, name="staff-team-reject"),
    path("team/<uuid:user_id>/role/", views.team_change_role, name="staff-team-role"),
    path("team/<uuid:user_id>/remove/", views.team_remove, name="staff-team-remove"),
    path("team/<uuid:user_id>/restore/", views.team_restore, name="staff-team-restore"),
    path("join/<str:token>/", views.staff_join, name="staff-join"),
]
