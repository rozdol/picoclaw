from app.config import SETTINGS


def is_user_allowed(user_id: int | None) -> bool:
    print(f"⚠️ Checking if user_id {user_id} is allowed...")
    if user_id is None:
        return False
    if not SETTINGS.allowed_user_ids:
        return False
    return user_id in SETTINGS.allowed_user_ids
