LEVELS = {"admin": {"public", "private"}, "viewer": {"public"}}


def check_access(role: str, secret_level: str) -> bool:
    if role not in LEVELS:
        raise ValueError("ACCESS_UNKNOWN_ROLE")
    return secret_level in LEVELS[role]
