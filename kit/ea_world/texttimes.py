"""Find times in prose, with the zone label and date they refer to.

Used by `check_email` (time_without_zone, wrong_local_time) and by graders (local_times_correct,
proposed_times_valid, sent_matches_edit). Zone abbreviations are read leniently by region
("MST" means Denver time), because the trap being tested is the hour, not the label.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ZONE_ALIASES: dict[str, str] = {
    "denver": "America/Denver", "mountain time": "America/Denver", "mountain": "America/Denver",
    "mt": "America/Denver", "mst": "America/Denver", "mdt": "America/Denver",
    "chicago": "America/Chicago", "austin": "America/Chicago", "central time": "America/Chicago",
    "central": "America/Chicago", "ct": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago",
    "new york": "America/New_York", "eastern time": "America/New_York", "eastern": "America/New_York",
    "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York", "nyc": "America/New_York",
    "london": "Europe/London", "uk time": "Europe/London", "uk": "Europe/London", "gmt": "Europe/London",
    "bst": "Europe/London", "british time": "Europe/London",
    "utc": "UTC",
    "your time": "recipient", "your local time": "recipient", "your end": "recipient",
    "my time": "sender",
}  # fmt: skip

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}  # fmt: skip
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
    "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}  # fmt: skip

_ZONE_RX = "|".join(sorted((re.escape(z) for z in ZONE_ALIASES), key=len, reverse=True))
_TIME = r"(?P<h{n}>\d{{1,2}})(?:[:.](?P<m{n}>\d{{2}}))?\s?(?P<ap{n}>a\.?m\.?|p\.?m\.?)?|(?P<noon{n}>noon|midday)"
_T1 = _TIME.format(n=1)
_T2 = _TIME.format(n=2)
TIME_RX = re.compile(
    rf"(?<![\w$.:/-])(?:{_T1})(?:\s?(?:-|–|—|to|until)\s?(?:{_T2}))?"
    rf"(?P<zone>\s?(?:\(|,)?\s?(?:in\s|on\s)?(?:{_ZONE_RX})(?:\stime)?\)?)?(?![\w])",
    re.IGNORECASE,
)
_ZONE_ONLY = re.compile(rf"(?:{_ZONE_RX})", re.IGNORECASE)
_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MO = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_RX = re.compile(
    rf"(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})"
    rf"|(?P<wd>\b(?:{_WD})\b\.?)(?:,?\s(?:(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s(?P<m1>{_MO})\b|(?P<m2>{_MO})\.?\s(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?\b))?"
    rf"|\b(?P<d3>\d{{1,2}})(?:st|nd|rd|th)?\s(?P<m3>{_MO})\b"
    rf"|\b(?P<m4>{_MO})\.?\s(?P<d4>\d{{1,2}})(?:st|nd|rd|th)?\b"
    rf"|\b(?P<rel>today|tomorrow|tonight)\b",
    re.IGNORECASE,
)


@dataclass
class TimeMention:
    hour: int
    minute: int
    zone: str | None  # IANA name, "recipient", "sender" or None
    day: date | None
    weekday: int | None
    rel: str | None
    raw: str
    start: int
    end: int
    range_end: tuple[int, int] | None = None  # (hour, minute) of the end of a range

    def resolve(self, ref: datetime, recipient_tz: str | None = None, sender_tz: str = "America/Denver",
                prefer_next_week: bool = False) -> datetime | None:
        """An aware datetime, or None if the zone or date can't be settled."""
        tz = self.zone_name(recipient_tz, sender_tz)
        if tz is None:
            return None
        d = self.resolve_date(ref.astimezone(ZoneInfo(tz)).date(), prefer_next_week)
        if d is None:
            return None
        return datetime.combine(d, time(self.hour, self.minute), tzinfo=ZoneInfo(tz))

    def zone_name(self, recipient_tz: str | None, sender_tz: str = "America/Denver") -> str | None:
        if self.zone == "recipient":
            return recipient_tz
        if self.zone == "sender":
            return sender_tz
        return self.zone

    def resolve_date(self, today: date, prefer_next_week: bool = False) -> date | None:
        if self.day:
            return self.day
        if self.rel == "today" or self.rel == "tonight":
            return today
        if self.rel == "tomorrow":
            return today + timedelta(days=1)
        if self.weekday is not None:
            delta = (self.weekday - today.weekday()) % 7
            d = today + timedelta(days=delta)
            if prefer_next_week and d < today + timedelta(days=7 - today.weekday()):
                d += timedelta(days=7)
            return d
        return None


def _hm(h: str | None, m: str | None, ap: str | None, noon: str | None) -> tuple[int, int] | None:
    if noon:
        return 12, 0
    if h is None:
        return None
    hour, minute = int(h), int(m or 0)
    if ap:
        a = ap.lower().replace(".", "")
        if hour < 1 or hour > 12:
            return None
        if a == "pm" and hour != 12:
            hour += 12
        if a == "am" and hour == 12:
            hour = 0
    elif m is None:
        return None  # a bare number isn't a time
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _date_from(m: re.Match[str], year: int) -> tuple[date | None, int | None, str | None]:
    if m.group("iso"):
        try:
            return date.fromisoformat(m.group("iso")), None, None
        except ValueError:
            return None, None, None
    if m.group("rel"):
        return None, None, m.group("rel").lower()
    for d_key, m_key in (("d1", "m1"), ("d2", "m2"), ("d3", "m3"), ("d4", "m4")):
        if m.group(d_key) and m.group(m_key):
            try:
                return date(year, MONTHS[m.group(m_key).lower().rstrip(".")], int(m.group(d_key))), None, None
            except ValueError:
                return None, None, None
    if m.group("wd"):
        return None, WEEKDAYS[m.group("wd").lower().rstrip(".")], None
    return None, None, None


def find_times(text: str, year: int = 2026) -> list[TimeMention]:
    """Every time of day in the text, with its zone label and the date or weekday it belongs to.

    A date is taken from just before the time (since the previous time), else from just after it
    ("10am on Monday 2 November"), else inherited from the previous time on the same line.
    """
    out: list[TimeMention] = []
    if not text:
        return out
    dates = [(m.start(), m.end(), *_date_from(m, year)) for m in DATE_RX.finditer(text)]
    dates = [d for d in dates if d[2] or d[3] is not None or d[4]]
    used: set[int] = set()
    matches = []
    for m in TIME_RX.finditer(text):
        t2 = None
        if m.group("h2") or m.group("noon2"):
            t2 = _hm(m.group("h2"), m.group("m2"), m.group("ap2") or m.group("ap1"), m.group("noon2"))
        t1 = _hm(m.group("h1"), m.group("m1"), m.group("ap1"), m.group("noon1"))
        if t1 is None and t2 is not None and m.group("h1") and m.group("ap2"):
            t1 = _hm(m.group("h1"), m.group("m1"), m.group("ap2"), None)  # "12-1pm"
        if t1 is None:
            continue
        matches.append((m, t1, t2))
    last_ctx: tuple[date | None, int | None, str | None] = (None, None, None)
    last_line = -1
    prev_end = 0
    for i, (m, t1, t2) in enumerate(matches):
        zone = None
        if m.group("zone"):
            z = _ZONE_ONLY.search(m.group("zone"))
            if z:
                zone = ZONE_ALIASES[z.group(0).lower()]
        end = m.end()
        if zone in (None, "sender", "recipient"):
            after = re.match(rf"\s?\(\s?(?P<z>{_ZONE_RX})(?:\stime)?\s?\)", text[end:], re.IGNORECASE)
            if after:
                zone = ZONE_ALIASES[after.group("z").lower()]
                end += after.end()
        line_start = text.rfind("\n", 0, m.start()) + 1
        if line_start != last_line:
            last_ctx, last_line = (None, None, None), line_start
        next_start = matches[i + 1][0].start() if i + 1 < len(matches) else len(text)
        ctx = None
        before = [d for d in dates if d[0] >= max(prev_end, line_start) and d[1] <= m.start() and d[0] not in used]
        if before:
            ctx = before[-1][2:]
            used.add(before[-1][0])
        else:
            after_dates = [d for d in dates if d[0] >= end and d[0] - end <= 30 and d[1] <= next_start and d[0] not in used]
            if after_dates and "\n" not in text[end : after_dates[0][0]]:
                ctx = after_dates[0][2:]
                used.add(after_dates[0][0])
        if ctx is None:
            ctx = last_ctx
        last_ctx = ctx
        prev_end = end
        out.append(TimeMention(t1[0], t1[1], zone, ctx[0], ctx[1], ctx[2], text[m.start() : end], m.start(), end, t2))
    # A zone label after a list ("2pm or 3:30pm Central") applies to unlabelled times just before it.
    for i in range(len(out) - 1, 0, -1):
        if out[i - 1].zone is None and out[i].zone is not None:
            between = text[out[i - 1].end : out[i].start]
            if len(between) <= 12 and re.fullmatch(r"[\s,]*(?:or|and|/|,)?[\s,]*", between):
                out[i - 1].zone = out[i].zone
    return out
