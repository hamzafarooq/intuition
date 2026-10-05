"""Everything a grader can see about one trial: the case, the trace, the call log, the world before and after."""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from ea_harness.trace import parse_status, read_trace
from ea_world import paths
from ea_world.rules import resolve_person
from ea_world.state import State, read_jsonl


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub("[–—−]", "-", t).replace(" ", " ").replace(" ", " ")
    return re.sub(r"[ \t]+", " ", t)


def phrase_matches(phrase: str, text: str) -> bool:
    p, t = normalize(str(phrase)), normalize(text)
    if p.lower() in t.lower():
        return True
    try:
        return re.search(p, t, re.IGNORECASE) is not None
    except re.error:
        return False


@dataclass
class Verdict:
    value: str  # yes | no | cant_tell | na
    evidence: str = ""

    @staticmethod
    def yes(evidence: str = "") -> "Verdict":
        return Verdict("yes", evidence)

    @staticmethod
    def no(evidence: str = "") -> "Verdict":
        return Verdict("no", evidence)

    @staticmethod
    def cant(evidence: str = "") -> "Verdict":
        return Verdict("cant_tell", evidence)

    @staticmethod
    def of(ok: bool, yes_ev: str = "", no_ev: str = "") -> "Verdict":
        return Verdict("yes", yes_ev) if ok else Verdict("no", no_ev or yes_ev)


class Gold:
    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder or paths.gold_dir())
        self._cache: dict[str, Any] = {}

    def file(self, name: str) -> Any:
        if name not in self._cache:
            self._cache[name] = json.loads((self.folder / f"{name}.json").read_text(encoding="utf-8"))
        return self._cache[name]

    def ref(self, dotted: str) -> Any:
        """$gold.<file> gives the file's single list-valued key; $gold.<file>.<key> gives that key."""
        parts = dotted.split(".")
        data = self.file(parts[0])
        if len(parts) > 1:
            for p in parts[1:]:
                data = data[p]
            return data
        lists = [v for v in data.values() if isinstance(v, list)] if isinstance(data, dict) else []
        if isinstance(data, dict) and len(lists) == 1:
            return lists[0]
        return data


@dataclass
class TrialContext:
    case: dict[str, Any]
    run_dir: Path
    variant: dict[str, str] = field(default_factory=dict)
    gold: Gold = field(default_factory=Gold)
    verdicts: dict[str, str] = field(default_factory=dict)  # criterion key -> value, filled while grading

    # -- raw records ----------------------------------------------------------
    @cached_property
    def events(self) -> list[dict[str, Any]]:
        return read_trace(self.run_dir / "trace.jsonl")

    @cached_property
    def calls(self) -> list[dict[str, Any]]:
        return read_jsonl(self.run_dir / "calls.jsonl")

    @cached_property
    def approvals(self) -> list[dict[str, Any]]:
        return read_jsonl(self.run_dir / "approvals.jsonl")

    @cached_property
    def initial(self) -> State:
        return State(self.run_dir, "initial_state")

    @cached_property
    def final(self) -> State:
        sub = "final_state" if (self.run_dir / "final_state").exists() else "state"
        return State(self.run_dir, sub)

    @property
    def fill(self) -> dict[str, Any]:
        return self.case.get("fill") or {}

    @property
    def maya(self) -> dict[str, Any]:
        return self.case.get("maya") or {}

    @property
    def contacts(self) -> dict[str, Any]:
        return self.final.load("contacts")

    # -- people ---------------------------------------------------------------
    def address(self, value: str) -> str:
        p = resolve_person(value, self.contacts)
        return (p["email"] if p else value).lower()

    def addresses(self, values: Any) -> set[str]:
        if values is None:
            return set()
        if isinstance(values, str):
            values = [values]
        return {self.address(v) for v in values}

    def person(self, value: str) -> dict[str, Any] | None:
        return resolve_person(value, self.contacts)

    # -- trace views ----------------------------------------------------------
    def main(self, type_: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == type_ and e.get("agent", "main") == "main"]

    def of_type(self, type_: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == type_]

    def turn_texts(self) -> dict[int, str]:
        """Main-agent assistant text per turn."""
        out: dict[int, list[str]] = {}
        for e in self.main("assistant"):
            out.setdefault(int(e.get("turn", 0)), []).append(e.get("text", ""))
        return {t: "\n\n".join(v) for t, v in out.items()}

    def turn_finals(self) -> list[tuple[int, str, str]]:
        """(turn, last main text, status) per turn, using the status events."""
        texts = self.turn_texts()
        statuses = {int(e.get("turn", 0)): e.get("status", "missing") for e in self.main("status")}
        out = []
        for turn in sorted(set(texts) | set(statuses)):
            last = [e.get("text", "") for e in self.main("assistant") if int(e.get("turn", 0)) == turn]
            out.append((turn, last[-1] if last else "", statuses.get(turn) or parse_status(texts.get(turn, ""))[0]))
        return out

    def final_text(self) -> str:
        texts = self.turn_texts()
        if not texts:
            return ""
        last_turn = max(texts)
        last = [e.get("text", "") for e in self.main("assistant") if int(e.get("turn", 0)) == last_turn]
        return last[-1] if last else ""

    def replies(self) -> str:
        return "\n\n".join(e.get("text", "") for e in self.main("assistant"))

    def after_disconnect(self) -> str:
        seq = None
        for c in self.calls:
            if c.get("tool") == "connector_disconnect" and c.get("ok"):
                seq = c.get("seq")
                break
        if seq is None:
            return ""
        # find the trace position of that call's result
        pos = None
        for e in self.events:
            if e.get("type") == "tool_result" and e.get("tool") == "connector_disconnect" and e.get("ok"):
                pos = e["seq"]
                break
        if pos is None:
            disc_users = [e for e in self.events if e.get("type") == "signal" and e.get("kind") == "disconnect"]
            pos = disc_users[0]["seq"] if disc_users else None
        if pos is None:
            return ""
        return "\n\n".join(e.get("text", "") for e in self.main("assistant") if e["seq"] > pos)

    def final_status(self) -> str:
        st = self.main("status")
        return st[-1].get("status", "missing") if st else "missing"

    # -- world views ----------------------------------------------------------
    def initial_ids(self, name: str, key: str) -> set[str]:
        return {r["id"] for r in self.initial.load(name).get(key, [])}

    def created_events(self) -> list[dict[str, Any]]:
        before = self.initial_ids("calendars", "maya")
        return [e for e in self.final.load("calendars").get("maya", []) if e["id"] not in before and e.get("status") == "active"]

    def event(self, event_id: str, which: str = "final") -> dict[str, Any] | None:
        st = self.final if which == "final" else self.initial
        return next((e for e in st.load("calendars").get("maya", []) if e["id"] == event_id), None)

    def sent(self) -> list[dict[str, Any]]:
        before = self.initial_ids("outbox", "sent")
        return [m for m in self.final.load("outbox").get("sent", []) if m["id"] not in before]

    def drafts(self) -> list[dict[str, Any]]:
        before = self.initial_ids("drafts", "drafts")
        return [d for d in self.final.load("drafts").get("drafts", []) if d["id"] not in before]

    def latest_email_to(self, to: str) -> dict[str, Any] | None:
        addr = self.address(to)
        sent = [m for m in self.sent() if addr in {a.lower() for a in m.get("to", []) + m.get("cc", [])}]
        if sent:
            return sent[-1]
        drafts = [d for d in self.drafts() if addr in {a.lower() for a in d.get("to", [])}]
        drafts.sort(key=lambda d: (d.get("updated_at") or "", d["id"]))
        return drafts[-1] if drafts else None

    def bookings(self, which: str = "final") -> list[dict[str, Any]]:
        st = self.final if which == "final" else self.initial
        return st.load("bookings").get("bookings", [])

    def created_bookings(self, active_only: bool = True) -> list[dict[str, Any]]:
        before = self.initial_ids("bookings", "bookings")
        return [b for b in self.bookings() if b["id"] not in before and (b.get("status") == "active" or not active_only)]

    def briefs(self) -> list[dict[str, Any]]:
        from ea_world.outputs import all_briefs

        return all_briefs(self.run_dir)

    def decks(self) -> list[dict[str, Any]]:
        from ea_world.outputs import all_decks

        return all_decks(self.run_dir)

    def latest_brief(self) -> dict[str, Any] | None:
        b = self.briefs()
        return b[-1] if b else None

    def latest_deck(self) -> dict[str, Any] | None:
        d = self.decks()
        return d[-1] if d else None

    def target(self, name: str, to: str | None = None) -> str:
        if name == "final":
            return self.final_text()
        if name == "replies":
            return self.replies()
        if name == "after_disconnect":
            return self.after_disconnect()
        if name == "brief":
            b = self.latest_brief()
            return b.get("markdown", "") if b else ""
        if name == "deck":
            d = self.latest_deck()
            if not d:
                return ""
            return "\n".join([s.get("title", "") for s in d.get("slides", [])] + [b for s in d.get("slides", []) for b in s.get("bullets", [])])
        if name == "email" and to:
            m = self.latest_email_to(to)
            return (m.get("subject", "") + "\n" + m.get("body", "")) if m else ""
        if name.startswith("sent:"):
            addr = self.address(name.split(":", 1)[1])
            return "\n\n".join(m.get("subject", "") + "\n" + m.get("body", "") for m in self.sent()
                               if addr in {a.lower() for a in m.get("to", []) + m.get("cc", [])})
        raise ValueError(f"Unknown text target '{name}'")

    def now(self):
        return self.initial.now() if (self.run_dir / "initial_state").exists() else self.final.now()
