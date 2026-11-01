LEVELS = {"admin": {"public", "private"}, "viewer": {"public"}}


def check_access(role: str, secret_level: str) -> bool:
    return secret_level in LEVELS.get(role, set())
