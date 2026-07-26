from fastapi import Header, HTTPException

from app.core.config import settings


def _client_ids_by_key() -> dict[str, str]:
    """Map api key -> client_id from comma-separated key:client_id pairs."""
    entries = (entry.split(":", 1) for entry in settings.api_keys.split(",") if ":" in entry)
    return {key.strip(): client_id.strip() for key, client_id in entries}


def require_api_key(x_api_key: str = Header(default="")) -> str:
    client_id = _client_ids_by_key().get(x_api_key)
    if not x_api_key or client_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return client_id
