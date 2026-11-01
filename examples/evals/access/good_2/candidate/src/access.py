ALLOWED = {("admin", "public"), ("admin", "private"), ("viewer", "public")}


def check_access(role: str, secret_level: str) -> bool:
    if role not in ("admin", "viewer"):
        raise ValueError("ACCESS_UNKNOWN_ROLE")
    return (role, secret_level) in ALLOWED
