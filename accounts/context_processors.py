from .roles import MEMBER, OPERATOR, user_role


def roles(request):
    role = user_role(request.user)
    session = getattr(request, "session", None)
    return {
        "user_role": role,
        "is_member": role in (MEMBER, OPERATOR),
        "is_operator": role == OPERATOR,
        "is_guest": (not request.user.is_authenticated) and bool(session and session.get("guest")),
    }
