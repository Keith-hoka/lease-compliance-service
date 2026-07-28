import logging

from app.core.logs import configure_logging


def test_configure_logging_surfaces_info():
    configure_logging()
    assert logging.getLogger("app.clause_audit").isEnabledFor(logging.INFO)
    assert logging.getLogger().handlers


def test_configure_logging_is_idempotent():
    configure_logging()
    handlers_before = list(logging.getLogger().handlers)
    configure_logging()
    assert logging.getLogger().handlers == handlers_before
