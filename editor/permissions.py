from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import EditSession, SiteMembership


def session_for_user(user, session_id, *, require_publish=False) -> EditSession:
    edit_session = get_object_or_404(EditSession.objects.select_related("site", "owner"), pk=session_id)
    membership = SiteMembership.objects.filter(site=edit_session.site, user=user).first()
    if not membership and not user.is_superuser:
        raise PermissionDenied
    if require_publish and not user.is_superuser and membership.role != SiteMembership.Role.PUBLISHER:
        raise PermissionDenied("Nie masz uprawnienia do publikowania tej strony.")
    return edit_session
