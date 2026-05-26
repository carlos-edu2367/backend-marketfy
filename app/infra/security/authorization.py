from typing import Any, List


def get_user_role(user: Any) -> str:
    role = getattr(user, "role", "")
    return role.value if hasattr(role, "value") else str(role)


def is_admin_user(user: Any) -> bool:
    return get_user_role(user) == "admin"


def require_admin_user(user: Any) -> Any:
    if not is_admin_user(user):
        raise PermissionError("Acesso negado. Apenas administradores.")
    return user


def can_reply_to_ticket(ticket: Any, user: Any) -> bool:
    return is_admin_user(user) or getattr(ticket, "requester_id", None) == getattr(user, "id", None)


def filter_ticket_messages_for_user(ticket: Any, user: Any) -> List[Any]:
    messages = list(getattr(ticket, "messages", []) or [])
    if is_admin_user(user):
        return messages
    return [message for message in messages if not getattr(message, "is_internal", False)]
