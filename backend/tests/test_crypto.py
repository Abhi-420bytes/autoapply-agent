from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_config
from app.security import crypto


@pytest.fixture
def swap_keys(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Set MASTER_KEY / MASTER_KEY_PREVIOUS for one test, then restore."""

    def _set(primary: str, previous: str = "") -> None:
        monkeypatch.setenv("MASTER_KEY", primary)
        monkeypatch.setenv("MASTER_KEY_PREVIOUS", previous)
        get_config.cache_clear()
        crypto.reset_cipher_cache()

    yield _set
    monkeypatch.undo()
    get_config.cache_clear()
    crypto.reset_cipher_cache()


def test_roundtrip_and_ciphertext_is_not_plaintext() -> None:
    ct = crypto.encrypt("sk-ant-secret-value-1234")
    assert "secret" not in ct
    assert crypto.decrypt(ct) == "sk-ant-secret-value-1234"
    assert crypto.encrypt("same") != crypto.encrypt("same")  # random IV


def test_json_roundtrip() -> None:
    data = {"access_token": "abc", "refresh_token": "def", "expires_in": 3600}
    assert crypto.decrypt_json(crypto.encrypt_json(data)) == data


def test_wrong_key_fails_cleanly(swap_keys) -> None:  # type: ignore[no-untyped-def]
    ct = crypto.encrypt("hello")
    swap_keys(Fernet.generate_key().decode())
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(ct)


def test_previous_key_still_decrypts_and_rotate_moves_to_new_key(swap_keys) -> None:  # type: ignore[no-untyped-def]
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    swap_keys(old)
    ct_old = crypto.encrypt("portal-password")
    swap_keys(new, previous=old)
    assert crypto.decrypt(ct_old) == "portal-password"
    ct_new = crypto.rotate(ct_old)
    swap_keys(new)  # old key retired
    assert crypto.decrypt(ct_new) == "portal-password"
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(ct_old)


def test_invalid_master_key_error_does_not_echo_key(swap_keys) -> None:  # type: ignore[no-untyped-def]
    swap_keys("not-a-valid-key-SUPERSECRET")
    with pytest.raises(ValueError) as exc:
        crypto.encrypt("x")
    assert "SUPERSECRET" not in str(exc.value)


@pytest.mark.parametrize(
    ("secret", "masked"),
    [
        (None, None),
        ("short", "••••"),
        ("sk-proj-abcdefghijklmnopa1b2", "sk-...a1b2"),
        ("sk-ant-api03-xxxxxxxxxxxxxxxxwxyz", "sk-...wxyz"),
        ("gsk_abcdefghijklmnop9876", "gsk_...9876"),
        ("AIzaSyAbcdefghijklmnopqrstu", "...rstu"),
    ],
)
def test_mask_secret(secret: str | None, masked: str | None) -> None:
    assert crypto.mask_secret(secret) == masked


def test_mask_never_reveals_more_than_prefix_and_last4() -> None:
    secret = "sk-" + "A" * 20 + "Z" * 4
    masked = crypto.mask_secret(secret)
    assert masked is not None
    assert "A" not in masked
