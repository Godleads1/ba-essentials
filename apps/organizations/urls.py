from django.urls import path

from . import views

urlpatterns = [
    path("onboarding/", views.onboarding, name="organization-onboarding"),
    path("team/", views.team_home, name="team-home"),
    path("team/invite/", views.invite_member, name="team-invite"),
    path(
        "team/invitations/<uuid:invitation_id>/revoke/",
        views.revoke_invitation,
        name="team-invitation-revoke",
    ),
    path(
        "team/members/<uuid:membership_id>/remove/",
        views.remove_member,
        name="team-member-remove",
    ),
    path(
        "switch-org/<uuid:organization_id>/",
        views.switch_organization,
        name="switch-organization",
    ),
]
