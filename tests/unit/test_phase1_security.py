import os
import sys
import uuid

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.identity import CPF, Email, User, UserRole
from domain.support import Ticket


def make_user(role=UserRole.OWNER):
    return User(
        name="User",
        email=Email(f"{uuid.uuid4()}@marketfy.test"),
        cpf=CPF("12345678900"),
        password_hash="hash",
        role=role,
    )


def test_require_admin_user_rejects_owner():
    from infra.security.authorization import require_admin_user

    with pytest.raises(PermissionError):
        require_admin_user(make_user(UserRole.OWNER))


def test_require_admin_user_allows_admin():
    from infra.security.authorization import require_admin_user

    admin = make_user(UserRole.ADMIN)

    assert require_admin_user(admin) is admin


def test_ticket_reply_allowed_only_for_requester_or_admin():
    from infra.security.authorization import can_reply_to_ticket

    requester = make_user(UserRole.OWNER)
    other_user = make_user(UserRole.OWNER)
    admin = make_user(UserRole.ADMIN)
    ticket = Ticket(
        requester_id=requester.id,
        market_id=None,
        subject="Preciso de ajuda",
    )

    assert can_reply_to_ticket(ticket, requester) is True
    assert can_reply_to_ticket(ticket, admin) is True
    assert can_reply_to_ticket(ticket, other_user) is False


def test_ticket_messages_for_owner_hide_internal_messages():
    from infra.security.authorization import filter_ticket_messages_for_user

    requester = make_user(UserRole.OWNER)
    admin = make_user(UserRole.ADMIN)
    ticket = Ticket(
        requester_id=requester.id,
        market_id=None,
        subject="Preciso de ajuda",
    )
    ticket.add_message(requester.id, "Mensagem publica")
    ticket.add_message(admin.id, "Nota interna", is_internal=True)

    owner_messages = filter_ticket_messages_for_user(ticket, requester)
    admin_messages = filter_ticket_messages_for_user(ticket, admin)

    assert [message.content for message in owner_messages] == ["Mensagem publica"]
    assert [message.content for message in admin_messages] == ["Mensagem publica", "Nota interna"]


def test_in_memory_rate_limiter_blocks_after_limit():
    from infra.security.rate_limiter import InMemoryRateLimiter

    limiter = InMemoryRateLimiter()

    assert limiter.hit("login:127.0.0.1", limit=2, window_seconds=60) is True
    assert limiter.hit("login:127.0.0.1", limit=2, window_seconds=60) is True
    assert limiter.hit("login:127.0.0.1", limit=2, window_seconds=60) is False


def test_validate_pfx_upload_rejects_invalid_extension():
    from infra.security.uploads import validate_pfx_upload

    with pytest.raises(ValueError):
        validate_pfx_upload("certificado.txt", "application/octet-stream", 100)


def test_validate_pfx_upload_rejects_files_above_two_mb():
    from infra.security.uploads import validate_pfx_upload

    with pytest.raises(ValueError):
        validate_pfx_upload("certificado.pfx", "application/octet-stream", 2 * 1024 * 1024 + 1)


def test_validate_pfx_upload_generates_server_filename():
    from infra.security.uploads import validate_pfx_upload

    market_id = uuid.uuid4()
    safe_name = validate_pfx_upload("../certificado.pfx", "application/x-pkcs12", 128, market_id)

    assert safe_name.endswith(".pfx")
    assert ".." not in safe_name
    assert "certificado" not in safe_name
    assert str(market_id) in safe_name


def test_secret_cipher_does_not_store_plain_text():
    from infra.security.secret_cipher import SecretCipher

    cipher = SecretCipher("phase1-test-key")

    encrypted = cipher.encrypt("segredo-fiscal")

    assert encrypted != "segredo-fiscal"
    assert cipher.decrypt(encrypted) == "segredo-fiscal"
