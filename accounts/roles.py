"""Single source of truth for POLARIS access levels.

visitor  -> not logged in
member   -> registered account (AI assistant, forecasts)
operator -> full access (satellite data, IoT sites, manual messages)

Superusers are always operators.
"""

VISITOR = "visitor"
MEMBER = "member"
OPERATOR = "operator"


def user_role(user):
    if not user.is_authenticated:
        return VISITOR
    if user.is_superuser:
        return OPERATOR
    profile = getattr(user, "profile", None)
    return profile.role if profile else MEMBER
