from datetime import date, datetime
from zoneinfo import ZoneInfo


def sydney_today() -> date:
    """Today in the service's operating timezone."""
    return datetime.now(tz=ZoneInfo("Australia/Sydney")).date()
