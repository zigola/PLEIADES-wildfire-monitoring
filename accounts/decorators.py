from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from .roles import MEMBER, OPERATOR, user_role


def role_required(*allowed_roles):
    """Anonymous -> login page (with ?next=). Wrong role -> 403 page."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if user_role(request.user) not in allowed_roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


member_required = role_required(MEMBER, OPERATOR)
operator_required = role_required(OPERATOR)
