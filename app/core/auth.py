from fastapi import Header, HTTPException

from app.core.config import settings


def require_api_key(x_api_key: str = Header(default="")) -> None:
    keys = {k.strip() for k in settings.api_keys.split(",") if k.strip()}
    if x_api_key not in keys:
        raise HTTPException(status_code=401, detail="Invalid API key")
