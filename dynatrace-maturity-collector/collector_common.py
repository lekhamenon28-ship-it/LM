"""Vendor-neutral time parsing used by independent collectors."""
from datetime import datetime, timezone
from typing import Any

def timestamp(value: Any) -> datetime | None:
    if value in (None, "", 0, "0", -1, "-1"):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            return datetime.fromtimestamp(number, timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None
