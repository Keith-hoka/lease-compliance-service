import pytest
from fastapi import HTTPException

from app.core.auth import require_api_key
from app.core.config import settings


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "abc123:rentalapp, xyz789:acme")


def test_valid_key_returns_client_id():
    assert require_api_key("abc123") == "rentalapp"


def test_second_key_maps_to_its_tenant():
    assert require_api_key("xyz789") == "acme"


def test_unknown_key_is_401():
    with pytest.raises(HTTPException) as excinfo:
        require_api_key("nope")
    assert excinfo.value.status_code == 401


def test_missing_key_is_401():
    with pytest.raises(HTTPException):
        require_api_key("")


def test_unlabelled_entry_is_unusable(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "bare-key")
    with pytest.raises(HTTPException):
        require_api_key("bare-key")
