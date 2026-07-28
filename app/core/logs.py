"""Root logging setup so app INFO lines reach stderr under uvicorn."""

import logging


def configure_logging() -> None:
    """Idempotent: basicConfig is a no-op once the root logger has handlers."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger().setLevel(logging.INFO)
