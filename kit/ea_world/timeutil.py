"""Time helpers. Every conversion goes through zoneinfo; nothing assumes a fixed offset."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mcp.server.mcpserver.exceptions import ToolError

DENVER = "America/Denver"

ZONE_LABELS = {
    "America/Denver": "Denver",
    "America/New_York": "New York",
    "America/Chicago": "Chicago",
    "Europe/London": "London",
}


def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def parse_dt(value: str, field: str = "time", default_tz: str | None = None) -> datetime:
    """Parse an ISO 8601 time. It must carry an offset, unless `default_tz` names a zone to apply."""
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"Missing {field}. Use ISO 8601 with an offset, e.g. 2026-10-28T11:00:00-06:00.")
    text = value.strip().replace("Z", "+00:00")
    zone_name = None
    if "[" in text and text.endswith("]"):
        text, zone_name = text[:-1].split("[", 1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolError(
            f"Can't read {field} '{value}'. Use ISO 8601 with an offset, e.g. 2026-10-28T11:00:00-06:00."
        ) from exc
    if zone_name:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(zone_name)) if dt.tzinfo is None else dt.astimezone(ZoneInfo(zone_name))
        except Exception as exc:
            raise ToolError(f"Unknown time zone '{zone_name}' in {field}.") from exc
    if dt.tzinfo is None:
        if default_tz:
            return dt.replace(tzinfo=ZoneInfo(default_tz))
        raise ToolError(
            f"{field} '{value}' has no offset or time zone. Use ISO 8601 with an offset, "
            "e.g. 2026-10-28T11:00:00-06:00, or append a zone name like [America/Denver]."
        )
    return dt


def parse_window_bound(value: str, field: str, tz: str, end: bool = False) -> datetime:
    """Window bounds may be a bare date (whole day in `tz`) or a full ISO time with offset."""
    v = (value or "").strip()
    if len(v) == 10:
        try:
            d = date.fromisoformat(v)
        except ValueError as exc:
            raise ToolError(f"Can't read {field} '{value}'. Use YYYY-MM-DD or ISO 8601 with an offset.") from exc
        base = datetime.combine(d, time(0, 0), tzinfo=ZoneInfo(tz))
        return base + timedelta(days=1) if end else base
    return parse_dt(v, field)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def to_local(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz))


def local_iso(dt: datetime, tz: str) -> str:
    return iso(dt.astimezone(ZoneInfo(tz)))


def hhmm(t: str) -> time:
    h, m = t.split(":")
    return time(int(h), int(m))


def label(tz: str) -> str:
    return ZONE_LABELS.get(tz, tz)


def fmt_local(dt: datetime, tz: str) -> str:
    """'13:00 New York' style label used in tool results."""
    return f"{dt.astimezone(ZoneInfo(tz)).strftime('%H:%M')} {label(tz)}"


def utc_offset_hours(tz: str, at: datetime) -> float:
    off = at.astimezone(ZoneInfo(tz)).utcoffset()
    return (off.total_seconds() / 3600) if off is not None else 0.0
