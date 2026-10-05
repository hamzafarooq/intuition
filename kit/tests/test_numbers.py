"""Number tracing (`ea_world/numbers.py`, spec/03-mcp-server.md "Number tracing"): extraction and
normalization of money, percentages and counts with units; years, dates, times and list numbering
are ignored."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ea_world import numbers


def key(text: str) -> str:
    found = numbers.extract(text)
    assert len(found) == 1, f"{text!r} -> {[n.key for n in found]}"
    return found[0].key


# ---------------------------------------------------------------- canonical values


@pytest.mark.parametrize(
    ("text", "kind", "value", "unit"),
    [
        ("$4.2M", "money", Decimal(4_200_000), ""),
        ("$4,200,000", "money", Decimal(4_200_000), ""),
        ("$610K", "money", Decimal(610_000), ""),
        ("$12,500", "money", Decimal(12_500), ""),
        ("$0.9M", "money", Decimal(900_000), ""),
        ("4.2 million dollars", "money", Decimal(4_200_000), ""),
        ("8%", "pct", Decimal("0.08"), ""),
        ("3 late shipments", "count", Decimal(3), "shipment"),
        ("6 days", "count", Decimal(6), "day"),
    ],
)
def test_extract_canonical_value(text: str, kind: str, value: Decimal, unit: str) -> None:
    (n,) = numbers.extract(text)
    assert n.kind == kind
    assert n.value == value
    assert n.unit == unit


def test_money_key_matches_the_spec_examples() -> None:
    assert key("$4.2M") == "money:4200000"
    assert key("8%") == "pct:0.08"
    assert key("three late shipments") == "count:3:shipment"


# ---------------------------------------------------------------- equivalences


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("$4.2M", "$4,200,000"),
        ("$4.2M", "4.2 million dollars"),
        ("$4.2M", "$4.2 million"),
        ("$4.2M", "$4.2m"),
        ("$610K", "$610,000"),
        ("$12,500", "$12.5K"),
        ("$12,500", "$12500"),
        ("8%", "8 percent"),
        ("8%", "8.0%"),
        ("three late shipments", "3 shipments"),
        ("three late shipments", "3 late shipments"),
        ("six days", "6 days"),
        ("18 months", "an 18-month commitment"),
    ],
)
def test_equivalent_forms_normalize_alike(a: str, b: str) -> None:
    assert key(a) == key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("$4.2M", "$4.2K"),
        ("$260K", "$260"),  # a deal value vs the hotel cap
        ("8%", "0.8%"),
        ("8%", "$8"),
        ("3 shipments", "3 days"),
        ("3 shipments", "4 shipments"),
    ],
)
def test_different_values_stay_different(a: str, b: str) -> None:
    assert key(a) != key(b)


# ---------------------------------------------------------------- ignored


@pytest.mark.parametrize(
    "text",
    [
        "2026",
        "construction starts in Q2 2027",
        "the contract ends 31 December 2026",
        "on 2026-10-28",
        "Wed 28 Oct",
        "3–4 November",
        "11:00",
        "at 5pm",
        "17:00 London",
        "13:00–15:00",
        "1) Q3 delivery delays\n2) 2027 renewal timeline\n3) Aurora warehouse project",
        "1. Pipeline\n2. Forecast",
        "POs 4471, 4479, 4502",
    ],
)
def test_years_dates_times_and_list_numbering_are_ignored(text: str) -> None:
    assert numbers.extract(text) == []


def test_extract_from_prose_in_order() -> None:
    text = ("1) Q3: three late shipments on 19 October 2026 at 15:00, average delay of 6 days; "
            "the $12,500 credit is agreed. Fastlane cut prices 8%. West Q4 forecast: $4.2M.")
    found = numbers.extract(text)
    assert [n.key for n in found] == ["count:3:shipment", "count:6:day", "money:12500", "pct:0.08", "money:4200000"]
    assert [n.raw for n in found][2:] == ["$12,500", "8%", "$4.2M"]


# ---------------------------------------------------------------- tracing


def test_untraced_against_source_keys() -> None:
    sources = numbers.keys("Amy: three late shipments (POs 4471, 4479, 4502), the $12,500 credit you agreed. "
                           "Priya: West Q4 forecast $4.2M.")
    brief = "We owe Ridgeway $12.5K for 3 shipments; forecast $4,200,000; Fastlane cut 9%."
    missing = numbers.untraced(brief, sources)
    assert [n.raw for n in missing] == ["9%"]


def test_keys_of_empty_text() -> None:
    assert numbers.keys("") == set()
    assert numbers.untraced("", {"money:1"}) == []
