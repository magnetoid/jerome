import pytest
from cryptography.fernet import Fernet

from jerome.config import Settings
from jerome.core.secretbox import SecretBox, SecretUnreadable, secret_box, secret_storage_state


def test_round_trip_and_ciphertext_hides_plain():
    box = SecretBox(Fernet.generate_key().decode())
    token = box.encrypt("sk-ant-test-123")
    assert b"sk-ant-test-123" not in token
    assert box.decrypt(token) == "sk-ant-test-123"


def test_other_key_cannot_read():
    token = SecretBox(Fernet.generate_key().decode()).encrypt("x")
    with pytest.raises(SecretUnreadable):
        SecretBox(Fernet.generate_key().decode()).decrypt(token)


def test_secret_box_states():
    assert secret_box(Settings(_env_file=None)) is None
    assert secret_storage_state(Settings(_env_file=None)) == "missing"
    bad = Settings(_env_file=None, secret_key="not-a-fernet-key")
    assert secret_box(bad) is None and secret_storage_state(bad) == "invalid"
    good = Settings(_env_file=None, secret_key=Fernet.generate_key().decode())
    assert secret_box(good) is not None and secret_storage_state(good) == "ready"
