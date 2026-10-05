"""Number extraction and normalisation for grounding checks.

Extracts money (`$4.2M`, `$610K`, `$12,500`, `4.2 million dollars`), percentages (`8%`, `8 percent`)
and counts with fact-bearing units (`3 late shipments`, `three shipments`, `6 days`, `18 months`,
`0.4 miles`). Each becomes a canonical key such as `money:4200000`, `pct:0.08` or `count:3:shipment`.
Bare numbers, years, dates, times, quarters and list numbering are ignored.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}  # fmt: skip

# Units whose counts are facts worth tracing (not "3 emails" or "5 slides", which the assistant counts).
COUNT_UNITS = {
    "shipment": "shipment", "shipments": "shipment", "day": "day", "days": "day",
    "month": "month", "months": "month", "week": "week", "weeks": "week",
    "mile": "mile", "miles": "mile", "deal": "deal", "deals": "deal",
}  # fmt: skip

SCALE = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mm": 1_000_000, "million": 1_000_000,
         "b": 1_000_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000}  # fmt: skip

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MONEY_PREFIX = re.compile(
    rf"(?:US\$|USD\s?|\$)\s?(?P<num>{_NUM})\s?(?P<scale>thousand|million|billion|bn|mm|[kKmMbB])?(?![A-Za-z])"
)
_MONEY_SUFFIX = re.compile(
    rf"(?<![\w$.])(?P<num>{_NUM})\s?(?P<scale>thousand|million|billion|[kKmM])?\s?(?:US\s)?(?:dollars|USD)\b",
    re.IGNORECASE,
)
_PCT = re.compile(rf"(?<![\w.])(?P<num>{_NUM})\s?(?:%|percent\b|per\s?cent\b)", re.IGNORECASE)
_WORDS = "|".join(WORD_NUMBERS)
_COUNT = re.compile(
    rf"(?<![\w$.,])(?P<num>{_NUM}|{_WORDS})(?:-|\s)(?:[A-Za-z]+(?:-|\s)){{0,2}}?(?P<unit>{'|'.join(COUNT_UNITS)})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Num:
    raw: str
    kind: str  # money | pct | count
    value: Decimal
    unit: str = ""
    start: int = 0

    @property
    def key(self) -> str:
        v = format(self.value.normalize(), "f")
        if "." in v:
            v = v.rstrip("0").rstrip(".")
        return f"{self.kind}:{v}" + (f":{self.unit}" if self.unit else "")


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def extract(text: str) -> list[Num]:
    """Every traceable number in the text, in order of appearance."""
    if not text:
        return []
    found: list[Num] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for rx in (_MONEY_PREFIX, _MONEY_SUFFIX):
        for m in rx.finditer(text):
            if not free(m.start(), m.end()):
                continue
            val = _dec(m.group("num"))
            if val is None:
                continue
            scale = (m.group("scale") or "").lower()
            val = val * SCALE.get(scale, 1)
            found.append(Num(m.group(0).strip(), "money", val, start=m.start()))
            taken.append((m.start(), m.end()))
    for m in _PCT.finditer(text):
        if not free(m.start(), m.end()):
            continue
        val = _dec(m.group("num"))
        if val is None:
            continue
        found.append(Num(m.group(0).strip(), "pct", val / 100, start=m.start()))
        taken.append((m.start(), m.end()))
    for m in _COUNT.finditer(text):
        if not free(m.start(), m.end()):
            continue
        n = m.group("num").lower()
        val = Decimal(WORD_NUMBERS[n]) if n in WORD_NUMBERS else _dec(n)
        if val is None:
            continue
        found.append(Num(m.group(0).strip(), "count", val, COUNT_UNITS[m.group("unit").lower()], start=m.start()))
        taken.append((m.start(), m.end()))
    found.sort(key=lambda n: n.start)
    return found


def keys(text: str) -> set[str]:
    return {n.key for n in extract(text)}


def untraced(text: str, source_keys: set[str]) -> list[Num]:
    """Numbers in `text` whose canonical value appears in no source."""
    return [n for n in extract(text) if n.key not in source_keys]
