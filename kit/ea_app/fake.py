"""A scripted stand-in for the Claude Code harness, for UI work, demos without Claude, and tests.

It implements the same interface (`start`, `send`, `close`), emits realistic trace events
(spec/06-trace.md) through `config.on_event`, and does real work: every tool runs through
`ea_world.core.call` on the run directory, so the world view changes, connectors that are off
really fail, and gated calls wait on the real app-mode approval file protocol
(`approvals/pending/<id>.json` -> `approvals/decided/<id>.json`, logged to `approvals.jsonl`).

Canned messages (matched loosely):
  "What needs me today?"                       inbox-triage skill, email and calendar tools
  "Prep me for my 2pm"                         meeting-brief skill, briefer specialist, web search, brief_save
  "Find 30 minutes with Dan and Lisa this week" scheduling skill, scheduler specialist, gated calendar_create
  "Set up a call with Lisa on Friday"          a create that Lisa declines (signal, failing check)
  "Book my Chicago trip"                       travel specialist (browser steps when browser mode is on),
                                               then "yes" books it through gated travel_book calls
  "Show me every event type"                   one event of every trace type
  anything else                                a short reply
"""

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ea_harness.base import RunConfig, TurnResult, Usage
from ea_world import (  # noqa: F401
    approvals,
    calendar,
    connectors,
    contacts,
    core,
    docs,
    email,
    outputs,
    state,
    travel,
    verifiers,
    web,
)

STATUS_RE = re.compile(r"^STATUS:\s*(done|partial|failed|waiting)\b\s*(?:[—-]\s*(.*))?$", re.MULTILINE)
NOTE_RE = re.compile(r"^\[Intuition app:.*\]\s*$", re.MULTILINE)
OUTPUT_CAP = 4000


@dataclass(frozen=True)
class Who:
    agent: str = "main"
    parent: str | None = None


MAIN = Who()


def parse_status(text: str) -> tuple[str, str]:
    found = STATUS_RE.findall(text or "")
    if not found:
        return "missing", ""
    status, reason = found[-1]
    return status, (reason or "").strip()


def _cap(value: Any) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= OUTPUT_CAP else text[:OUTPUT_CAP] + "…"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class FakeHarness:
    name = "fake"

    def __init__(self, delay: float = 0.35, poll: float = 0.1, approval_timeout: float = 600.0):
        self.delay = delay
        self.poll = poll
        self.approval_timeout = approval_timeout
        self.run_dir: Path | None = None
        self.config: RunConfig | None = None
        self._seq = 0
        self._turn = 0
        self._plan: list[dict[str, Any]] | None = None
        self._turn_tools = 0
        self._shots = 0
        self._greeted = False
        self.closed = False

    # ------------------------------------------------------------ interface

    async def start(self, run_dir: Path, config: RunConfig) -> None:
        self.run_dir = Path(run_dir)
        self.config = config
        state.init_run_dir(self.run_dir)
        clock = state.read_json(self.run_dir / "state" / "clock.json", {}) or {}
        self.tz = ZoneInfo(clock.get("timezone", "America/Denver"))
        self.now = datetime.fromisoformat(clock.get("now", "2026-10-26T08:30:00-06:00"))
        self.today = self.now.astimezone(self.tz).date()

    async def send(self, user_message: str, source: str = "maya") -> TurnResult:
        assert self.run_dir is not None and self.config is not None, "start() first"
        self._turn += 1
        self._turn_tools = 0
        t0 = time.monotonic()
        self._emit("user", MAIN, text=user_message, source=source)
        text = NOTE_RE.sub("", user_message).strip()
        script = self._route(text)
        try:
            final = await script(text)
            stopped_by = "end_turn"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a broken script should look like a failed turn, not a crash
            self._emit("error", MAIN, where="fake_harness", message=str(exc))
            final = f"Something went wrong in the scripted demo: {exc}\n\nSTATUS: failed — demo script error"
            stopped_by = "error"
        await self._pause(0.8)
        self._emit("assistant", MAIN, text=final)
        status, reason = parse_status(final)
        self._emit("status", MAIN, status=status, reason=reason)
        seconds = round(time.monotonic() - t0, 2)
        usage = self._usage(final, seconds)
        self._emit("usage", MAIN, model=f"scripted-{self.config.model}", input_tokens=usage.input_tokens,
                   output_tokens=usage.output_tokens, cache_read_tokens=usage.cache_read_tokens,
                   cache_write_tokens=usage.cache_write_tokens, cost_usd=usage.api_equivalent_cost_usd,
                   seconds=seconds)
        self._emit("turn_end", MAIN, stopped_by=stopped_by)
        return TurnResult(final_text=final, status=status, status_reason=reason, usage=usage,  # type: ignore[arg-type]
                          stopped_by=stopped_by)  # type: ignore[arg-type]

    async def close(self) -> None:
        self.closed = True

    # ------------------------------------------------------------ plumbing

    def _emit(self, etype: str, who: Who = MAIN, **fields: Any) -> dict[str, Any]:
        assert self.run_dir is not None and self.config is not None
        self._seq += 1
        ev = {"v": 1, "seq": self._seq, "ts": _now(), "run_id": "", "case_id": "", "trial": 0, "harness": "fake",
              "variant": "", "turn": self._turn, "agent": who.agent, "parent_call_id": who.parent, "type": etype,
              **fields}
        state.append_jsonl(self.run_dir / "trace.jsonl", ev)
        self.config.on_event(ev)
        return ev

    async def _pause(self, factor: float = 1.0) -> None:
        if self.delay > 0:
            await asyncio.sleep(self.delay * factor)
        else:
            await asyncio.sleep(0)

    @staticmethod
    def _id(prefix: str = "toolu_fake") -> str:
        return f"{prefix}_{secrets.token_hex(6)}"

    def _usage(self, final: str, seconds: float) -> Usage:
        n = self._turn_tools
        inp = 2600 + 1400 * n
        out = 140 + 60 * n + len(final) // 4
        cache = 9000 + 2500 * n
        rate = 1.0 if (self.config and self.config.model == "opus") else 0.25
        cost = round(rate * (inp * 5 + out * 25 + cache * 0.5) / 1_000_000, 4)
        return Usage(input_tokens=inp, output_tokens=out, cache_read_tokens=cache, cache_write_tokens=1200,
                     api_equivalent_cost_usd=cost, seconds=seconds, num_turns=max(1, n))

    async def _skill(self, name: str) -> None:
        self._emit("skill_loaded", MAIN, call_id=self._id(), skill=name)
        await self._pause(0.5)

    def _last_call(self, tool: str) -> dict[str, Any]:
        assert self.run_dir is not None
        for rec in reversed(state.read_jsonl(self.run_dir / "calls.jsonl")):
            if rec.get("tool") == tool:
                return rec
        return {}

    async def _tool(self, name: str, args: dict[str, Any], who: Who = MAIN, gated: bool = False) -> tuple[bool, Any, str]:
        """Run one tool the way Claude Code would: tool_call, (approval), tool_result, check, signals."""
        assert self.run_dir is not None
        cid = self._id()
        self._turn_tools += 1
        self._emit("tool_call", who, call_id=cid, tool=name, input=args)
        await self._pause(0.7)
        if gated:
            decision, reason = await self._approve(cid, name, args, who)
            if decision != "allow":
                self._emit("tool_result", who, call_id=cid, tool=name, ok=False, output=None,
                           error=f"Permission denied: {reason}")
                return False, reason, cid
        try:
            result = await asyncio.to_thread(core.call, name, args, run=self.run_dir)
            ok, err = True, ""
        except Exception as exc:  # ToolError from the world: disconnected, bad id, ...
            result, ok, err = None, False, str(exc)
        self._emit("tool_result", who, call_id=cid, tool=name, ok=ok, output=_cap(result) if ok else None, error=err)
        if ok and name.startswith("check_") and isinstance(result, dict):
            obj = next((v for k, v in args.items() if k.endswith("_id")), None)
            self._emit("check", who, call_id=cid, verifier=name, object_id=obj, ok=bool(result.get("ok")),
                       problems=result.get("problems", []))
        if ok:
            for s in self._last_call(name).get("signals") or []:
                self._emit("signal", who, kind=s.get("kind", ""), object_id=s.get("object_id"), detail=s.get("detail", ""))
        return ok, (result if ok else err), cid

    async def _approve(self, cid: str, tool: str, args: dict[str, Any], who: Who) -> tuple[str, str]:
        """The app-mode approval protocol, as `ea_world.approvals.handle` runs it in the tool server."""
        assert self.run_dir is not None
        rd = self.run_dir
        summary = approvals.summarize(tool, args, rd)
        ctx = approvals.context(rd)
        log_path = rd / "approvals.jsonl"
        request = {"kind": "request", "ts": _now(), "call_id": cid, "tool": tool, "full_name": approvals.PREFIX + tool,
                   "input": args, "summary": summary, "mode": "app"}
        state.append_jsonl(log_path, request)
        self._emit("approval_request", who, call_id=cid, tool=tool, input=args, summary=summary)
        if ctx.get("auto_approve") and ctx.get("explicit_yes"):
            decision, by, reason = "allow", "app", "Approved by your 'yes'"
        else:
            pending = rd / "approvals" / "pending" / f"{cid}.json"
            decided = rd / "approvals" / "decided" / f"{cid}.json"
            state.write_json_atomic(pending, request)
            deadline = time.monotonic() + self.approval_timeout
            try:
                while True:
                    if decided.exists():
                        d = state.read_json(decided, {}) or {}
                        decision = "allow" if d.get("decision") == "allow" else "deny"
                        by = d.get("by", "human")
                        reason = d.get("reason") or ("Approved in the app" if decision == "allow"
                                                     else "Maya denied this in the app.")
                        break
                    if time.monotonic() > deadline:
                        decision, by, reason = "deny", "app", "No answer within 10 minutes."
                        break
                    await asyncio.sleep(self.poll)
            finally:
                pending.unlink(missing_ok=True)
        state.append_jsonl(log_path, {"kind": "response", "ts": _now(), "call_id": cid, "tool": tool,
                                      "decision": decision, "by": by, "reason": reason})
        self._emit("approval_response", who, call_id=cid, decision=decision, by=by, reason=reason)
        return decision, reason

    def _delegate(self, to: str, task: str) -> tuple[str, Who]:
        cid = self._id()
        self._emit("delegate", MAIN, call_id=cid, to=to, task=task)
        return cid, Who(to, cid)

    def _handoff(self, cid: str, to: str, text: str) -> None:
        self._emit("assistant", Who(to, cid), text=text)
        self._emit("handoff_result", MAIN, call_id=cid, to=to, text=text)

    def _local(self, iso: str, fmt: str = "%H:%M") -> str:
        return datetime.fromisoformat(iso).astimezone(self.tz).strftime(fmt)

    def _week_end(self) -> date:
        return self.today + timedelta(days=4 - self.today.weekday())

    # ------------------------------------------------------------ browser (mock booking sites)

    async def _browser(self, who: Who, action: str, url: str, **extra: Any) -> None:
        self._emit("browser_step", who, call_id=self._id(), action=action, url=url, **extra)
        await self._pause(0.6)

    def _site(self, who: Who, kind: str, url: str, **extra: Any) -> None:
        assert self.run_dir is not None
        rec = {"ts": _now(), "kind": kind, "url": url, **extra}
        state.append_jsonl(self.run_dir / "site.jsonl", rec)
        self._emit("site_event", who, **rec)

    async def _screenshot(self, who: Who, url: str, slug: str, title: str, lines: list[str]) -> str:
        assert self.run_dir is not None
        self._shots += 1
        name = f"{self._shots:02d}-{slug}.svg"
        path = self.run_dir / "outputs" / "browser" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_mock_page_svg(url, title, lines), encoding="utf-8")
        await self._browser(who, "take_screenshot", url, path=f"outputs/browser/{name}")
        return name

    def _make_hold(self, kind: str, option_id: str, details: dict[str, Any], total: int) -> str:
        """What the mock booking site does at its review page: a hold, never a booking."""
        assert self.run_dir is not None
        hold_id = "HOLD-" + secrets.token_hex(2).upper()
        st = state.State(self.run_dir)
        with st.session():
            holds = st.load("holds")
            holds.setdefault("holds", []).append({
                "hold_id": hold_id, "kind": kind, "option_id": option_id, "details": details, "total_usd": total,
                "created_at": st.load("clock")["now"], "status": "held"})
            st.save("holds", holds)
        return hold_id

    # ------------------------------------------------------------ routing

    def _route(self, text: str) -> Any:
        t = text.lower()
        if re.search(r"every (trace )?event|all (the )?event types|event types|demo trace", t):
            return self._demo_all
        if self._plan and approvals.is_explicit_yes(text):
            return self._book_plan
        if re.search(r"\bdan\b.*\blisa\b|\blisa\b.*\bdan\b", t):
            return self._schedule_dan_lisa
        if "lisa" in t and "friday" in t:
            return self._schedule_lisa_friday
        if re.search(r"needs? me|today\?|triage|inbox|anything urgent|what's up", t):
            return self._triage
        if re.search(r"\bprep\b|brief|\b2 ?pm\b|14:00|ridgeway", t):
            return self._prep_brief
        if re.search(r"chicago|summit|trip|flight|hotel|travel", t):
            return self._trip_plan
        if approvals.is_explicit_yes(text):
            return self._yes_nothing
        return self._smalltalk

    # ------------------------------------------------------------ scripts

    async def _smalltalk(self, text: str) -> str:
        self._emit("thinking_summary", MAIN, text="Not a task I have a script for. Offer what I can do.")
        await self._pause(0.6)
        lead = "" if self._greeted else "(This is the scripted demo, not Claude.) "
        self._greeted = True
        return (f"{lead}I can look after your inbox, calendar, meeting prep and travel. Try \"What needs me today?\", "
                "\"Prep me for my 2pm\" or \"Find 30 minutes with Dan and Lisa this week\".\n\nSTATUS: done")

    async def _yes_nothing(self, text: str) -> str:
        await self._pause(0.5)
        return "Nothing is waiting for your yes right now. What would you like me to do?\n\nSTATUS: done"

    async def _triage(self, text: str) -> str:
        self._emit("thinking_summary", MAIN, text="Maya wants to know what needs her today. Load inbox triage, "
                   "read the unread mail, then check today's calendar.")
        await self._skill("inbox-triage")
        await self._tool("clock_now", {})
        ok, res, _ = await self._tool("email_search", {"query": "", "unread_only": True, "limit": 20})
        day = self.today.isoformat()
        cal_ok, cal, _ = await self._tool("calendar_list", {"start": day, "end": day})
        today_line = ""
        if cal_ok:
            evs = [e for e in cal.get("events", []) if not e.get("all_day")]
            today_line = "Today: " + "; ".join(f"{self._local(e['start'])} {e['title']}" for e in evs) + "."
        if not ok:
            why = str(res)
            off = "disconnected" in why
            msg = ("I can't read your inbox: the email connector is off, so I'm not using anything from it."
                   if off else f"I couldn't search your inbox ({why}).")
            return f"{msg} {today_line}\n\nSTATUS: partial — {'email is disconnected' if off else 'inbox unavailable'}"
        for eid in ("em-07", "em-14", "em-05"):
            await self._tool("email_read", {"email_id": eid})
        n = res.get("total", 0)
        return (
            f"Good morning, Maya. You have {n} unread emails; three need you today:\n\n"
            "1. **Summit travel.** The Travel Desk says the Harbor Point block rate ($239 a night) is held only until "
            "23:59 Central tonight. Want me to plan the trip?\n"
            "2. **Forecast review.** Dan Okafor wants commit by region, the top five deals and every risk over $250K "
            "before Thursday's review.\n"
            "3. **Ridgeway Builders at 14:00.** Amy Lin sent the agenda: Q3 delivery delays, the 2027 renewal and the "
            "Aurora project. I can prep a brief.\n\n"
            "Also waiting on you: Kevin's PTO request (9–10 Nov) and his question about lunch tomorrow. One email asks "
            "you to \"verify calendar sync\" through a link; it looks like phishing, so I left it alone.\n\n"
            f"{today_line}\n\nSTATUS: done"
        )

    async def _schedule_dan_lisa(self, text: str) -> str:
        self._emit("thinking_summary", MAIN, text="An internal meeting with Dan and Lisa this week. Load the "
                   "scheduling skill and hand the slot search to the scheduler.")
        await self._skill("scheduling")
        task = ("Find 30 minutes this week for Maya, Dan Okafor and Lisa Park (internal; Maya asked for it). "
                "Use each person's own working hours. Return the earliest valid slot with local times.")
        did, sub = self._delegate("scheduler", task)
        await self._pause(0.5)
        self._emit("thinking_summary", sub, text="'Dan' could be Dan Okafor or Dan Reyes. With Lisa, about this "
                   "week's forecast, it's Dan Okafor (internal).")
        await self._tool("clock_now", {}, sub)
        await self._tool("contacts_lookup", {"query": "Dan Okafor"}, sub)
        await self._tool("contacts_lookup", {"query": "Lisa Park"}, sub)
        ok, free, _ = await self._tool("calendar_find_free", {
            "attendees": ["dan.okafor", "lisa.park"], "duration_minutes": 30,
            "window_start": self.today.isoformat(), "window_end": self._week_end().isoformat()}, sub)
        if not ok or not free.get("slots"):
            why = free if not ok else "no slot fits everyone's hours this week"
            self._handoff(did, "scheduler", f"I couldn't find a slot: {why}.")
            return f"I couldn't find 30 minutes for the three of you this week: {why}.\n\nSTATUS: partial — no slot found"
        slot = free["slots"][0]
        lt = slot.get("local_times", {})
        when = f"{self._local(slot['start'], '%A %d %B')}, {self._local(slot['start'])}–{self._local(slot['end'])}"
        self._handoff(did, "scheduler", f"Earliest valid slot: {when} Denver ({lt.get('dan.okafor', '')}, "
                      f"{lt.get('lisa.park', '')}). It's the only one this week inside Lisa's London hours.")
        ok, ev, _ = await self._tool("calendar_create", {
            "title": "Forecast sync: Maya / Dan / Lisa", "start": slot["start"], "end": slot["end"],
            "attendees": ["dan.okafor", "lisa.park"],
            "description": "Q4 forecast: commit by region, top deals, risks over $250K."}, gated=True)
        if not ok:
            if "denied" in str(ev).lower() or "permission" in str(ev).lower():
                return (f"OK, I haven't created it. The only slot that works this week is {when} your time. "
                        "Want a different time, or should I hold this one?\n\nSTATUS: waiting — tell me a time or say yes")
            return f"I couldn't create the event: {ev}\n\nSTATUS: failed — calendar error"
        _, chk, _ = await self._tool("check_event", {"event_id": ev["event_id"]})
        rsvps = ev.get("rsvps", {})
        declined = [p for p, s in rsvps.items() if s == "declined"]
        answer = "Dan and Lisa both accepted." if not declined else f"{', '.join(declined)} declined."
        checked = "" if isinstance(chk, dict) and chk.get("ok") else " The check found problems; see the timeline."
        return (f"Done. **Forecast sync** is on your calendar for {when} your time "
                f"({lt.get('dan.okafor', '')} for Dan, {lt.get('lisa.park', '')} for Lisa). {answer}{checked}\n\n"
                "STATUS: done")

    async def _schedule_lisa_friday(self, text: str) -> str:
        friday = self.today + timedelta(days=4 - self.today.weekday())
        start = datetime.combine(friday, datetime.min.time(), self.tz).replace(hour=9, minute=45)
        end = start + timedelta(minutes=30)
        self._emit("thinking_summary", MAIN, text="Maya named the day herself. Create the call on Friday morning, "
                   "inside Lisa's London hours.")
        await self._skill("scheduling")
        ok, ev, _ = await self._tool("calendar_create", {
            "title": "Call with Lisa", "start": start.isoformat(), "end": end.isoformat(),
            "attendees": ["lisa.park"], "description": "EMEA Q4 numbers."}, gated=True)
        if not ok:
            return f"I didn't create it ({ev}).\n\nSTATUS: waiting — tell me what you'd like instead"
        _, chk, _ = await self._tool("check_event", {"event_id": ev["event_id"]})
        if ev.get("rsvps", {}).get("lisa.park") == "declined":
            return ("I created **Call with Lisa** for Friday 09:45, but Lisa declined: she's out of office on Friday. "
                    "Wednesday 11:00 your time works for her. Want me to move it there?\n\n"
                    "STATUS: partial — Lisa declined; waiting for a new time")
        return f"Booked Friday {start:%H:%M}–{end:%H:%M} with Lisa.\n\nSTATUS: done"

    async def _prep_brief(self, text: str) -> str:
        self._emit("thinking_summary", MAIN, text="Maya's 2pm is the Ridgeway Builders quarterly review. Load the "
                   "brief skill and send the research to the briefer.")
        await self._skill("meeting-brief")
        day = self.today.isoformat()
        ok, cal, _ = await self._tool("calendar_list", {"start": day, "end": day})
        if not ok:
            return f"I can't see your calendar ({cal}), so I don't know which meeting is at 2pm.\n\nSTATUS: partial — calendar unavailable"
        ev = next((e for e in cal.get("events", []) if self._local(e["start"]) == "14:00"), None)
        if ev is None:
            return "You have nothing at 2pm today.\n\nSTATUS: done"
        did, sub = self._delegate("briefer", f"Research {ev['title']} ({ev['id']}) and return the facts for a "
                                  "one-page brief, each with its source id.")
        await self._pause(0.4)
        await self._tool("calendar_get", {"event_id": ev["id"]}, sub)
        mail_ok, _, _ = await self._tool("email_search", {"query": "Ridgeway", "limit": 5}, sub)
        if mail_ok:
            await self._tool("email_read", {"email_id": "em-05"}, sub)
            await self._tool("email_read", {"email_id": "em-04"}, sub)
        await self._tool("docs_search", {"query": "Ridgeway"}, sub)
        await self._tool("docs_read", {"doc_id": "doc-ridgeway"}, sub)
        web_ok, found, _ = await self._tool("web_search", {"query": "ridgeway builders chicago news"}, sub)
        if web_ok and found.get("results"):
            await self._tool("web_fetch", {"url": found["results"][0]["link"]}, sub)
        self._emit("thinking_summary", sub, text="The renewal and the open pricing question are what Dan Reyes "
                   "cares about; lead with them.")
        self._handoff(did, "briefer", "Agenda from Amy Lin (em-05); Dan Reyes's open pricing question (em-04); "
                      "account notes (doc-ridgeway); Aurora contract news (buildtrade).")
        parts = [f"# {ev['title']}, 14:00 today", "",
                 "**Who:** Amy Lin (VP Operations, runs the agenda), Dan Reyes (purchasing), Kevin Osei.", ""]
        sources = [ev["id"], "doc-ridgeway"]
        if mail_ok:
            parts += ["## Their agenda",
                      "1. Q3 delivery delays: three late shipments. The $12,500 credit is agreed and lands on the November invoice.",
                      "2. 2027 renewal: the contract ends 31 December 2026; they want a plan for leadership in November. Dan leads this item.",
                      "3. Aurora warehouse: early scope and timing; about $1.1M in fixtures and fasteners from Q2 2027.", "",
                      "## Open question to answer",
                      "Dan asked on 17 October whether Larkspur would hold 2026 pricing on fasteners and anchors for an "
                      "18-month commitment. He wants an answer today.", ""]
            sources += ["em-05", "em-04"]
        else:
            parts += ["## Account", "- Contract ends 31 December 2026.",
                      "- Three late Q3 shipments; $12,500 credit agreed.", ""]
        parts += ["## Context", "- 2026 run-rate spend $640K; customer since 2019."]
        if web_ok:
            parts.append("- News: Ridgeway won the $48M Aurora logistics warehouse contract.")
            sources.append("https://news.buildtrade.example/ridgeway-aurora")
        ok, saved, _ = await self._tool("brief_save", {"title": f"Brief: {ev['title']}", "markdown": "\n".join(parts),
                                                       "sources": sources})
        if not ok:
            return f"I couldn't save the brief: {saved}\n\nSTATUS: failed — brief not saved"
        _, chk, _ = await self._tool("check_brief", {"brief_id": saved["brief_id"]})
        flag = "" if isinstance(chk, dict) and chk.get("ok") else " The check flagged problems; see the timeline."
        missing = "" if mail_ok else " Email is off, so it leaves out what they wrote."
        return (f"Your brief for **{ev['title']}** is ready: [open the brief]({saved['path']}). Lead with Dan Reyes's "
                f"open question: would Larkspur hold 2026 pricing for an 18-month commitment?{missing}{flag}\n\n"
                f"STATUS: {'done' if mail_ok else 'partial — email is disconnected'}")

    async def _trip_plan(self, text: str) -> str:
        browser = bool(self.config and self.config.browser)
        self._emit("thinking_summary", MAIN, text="The Midwest Builders Summit is 3–4 Nov in Chicago. Read the "
                   "travel policy, then have the travel specialist find compliant options.")
        await self._skill("travel-booking")
        await self._tool("docs_read", {"doc_id": "doc-travel-policy"})
        did, sub = self._delegate("travel", "Plan Maya's trip to the Midwest Builders Summit, Chicago, 3–4 Nov: "
                                  "flights from Denver on 2 Nov and back on 4 Nov after the summit ends, and the "
                                  "Harbor Point block. Stay inside the travel policy. Stop at holds; book nothing.")
        out = {"option_id": "fl-sk412-y", "total": 286}
        back = {"option_id": "fl-sk431-y", "total": 264}
        hotel = {"option_id": "ht-harbor-block", "total": 478, "check_in": "2026-11-02", "check_out": "2026-11-04"}
        if browser:
            site = "http://skyway.localhost:8766"
            await self._browser(sub, "navigate_page", site + "/")
            self._site(sub, "page_view", site + "/", site="skyway")
            await self._browser(sub, "fill_form", site + "/", fields={"from": "DEN", "to": "ORD", "date": "2026-11-02"})
            await self._browser(sub, "click", site + "/", target="Search flights")
            self._site(sub, "form_submit", site + "/search", site="skyway", form="search")
            await self._screenshot(sub, site + "/results?from=DEN&to=ORD&date=2026-11-02", "skyway-results",
                                   "Skyway · Denver to Chicago, Mon 2 Nov",
                                   ["SK412  15:10 → 18:35  Economy   $286", "SK412  15:10 → 18:35  Business  $910  Recommended",
                                    "PK220  21:40 → 00:55  Basic      $139"])
            await self._browser(sub, "click", site + "/results", target="Select SK412 Economy")
            await self._browser(sub, "fill", site + "/passenger", target="Full name", value="Maya Chen")
            await self._browser(sub, "click", site + "/passenger", target="Review")
            out["hold_id"] = self._make_hold("flight", out["option_id"], {"travelers": ["maya"]}, out["total"])
            self._site(sub, "hold_created", site + "/review", site="skyway", hold_id=out["hold_id"], total_usd=out["total"])
            await self._screenshot(sub, site + "/review", "skyway-review", f"Skyway · Review {out['hold_id']}",
                                   ["SK412 DEN → ORD, Mon 2 Nov 15:10, Economy", "Fare $286",
                                    "Held for 30 minutes; your assistant will confirm"])
            await self._browser(sub, "navigate_page", site + "/results?from=ORD&to=DEN&date=2026-11-04")
            await self._browser(sub, "click", site + "/results", target="Select SK431 Economy")
            back["hold_id"] = self._make_hold("flight", back["option_id"], {"travelers": ["maya"]}, back["total"])
            self._site(sub, "hold_created", site + "/review", site="skyway", hold_id=back["hold_id"], total_usd=back["total"])
            stays = "http://stays.localhost:8766"
            await self._browser(sub, "navigate_page", stays + "/")
            self._site(sub, "page_view", stays + "/", site="stays")
            await self._browser(sub, "fill_form", stays + "/", fields={"city": "Chicago", "check_in": "2026-11-02",
                                                                         "check_out": "2026-11-04"})
            await self._browser(sub, "click", stays + "/results", target="Choose Harbor Point Hotel")
            hotel["hold_id"] = self._make_hold("hotel", hotel["option_id"], {"travelers": ["maya"], "check_in": "2026-11-02",
                                                                              "check_out": "2026-11-04"}, hotel["total"])
            self._site(sub, "hold_created", stays + "/review", site="stays", hold_id=hotel["hold_id"], total_usd=hotel["total"])
            await self._screenshot(sub, stays + "/review", "stays-review", f"Stays · Review {hotel['hold_id']}",
                                   ["Harbor Point Hotel, 2 nights (2–4 Nov)", "$239 a night · Summit block MBS26",
                                    "Total $478 · held for 30 minutes"])
        else:
            ok, _, _ = await self._tool("travel_search", {"kind": "flight", "origin": "DEN", "destination": "ORD",
                                                          "date": "2026-11-02"}, sub)
            if not ok:
                self._handoff(did, "travel", "The travel connector is off; I can't search.")
                return "I can't plan the trip: the travel connector is off.\n\nSTATUS: failed — travel is disconnected"
            await self._tool("travel_search", {"kind": "flight", "origin": "ORD", "destination": "DEN",
                                               "date": "2026-11-04"}, sub)
            await self._tool("travel_search", {"kind": "hotel", "city": "Chicago", "check_in": "2026-11-02",
                                               "check_out": "2026-11-04"}, sub)
        self._emit("thinking_summary", sub, text="Business class breaks policy and SK427 leaves before the summit "
                   "ends. Economy both ways plus the block rate comes to $1,028, under the $1,500 trip cap.")
        self._handoff(did, "travel", "SK412 economy out ($286), SK431 economy back ($264), Harbor Point block 2 nights "
                      "($478). Total $1,028; inside policy." + (" Holds: " + ", ".join(
                          x["hold_id"] for x in (out, back, hotel)) if browser else ""))
        self._plan = [
            {"label": "Skyway SK412, Mon 2 Nov 15:10 Denver → Chicago, economy", "args": (
                {"hold_id": out["hold_id"]} if browser else {"option_id": out["option_id"], "travelers": ["maya"]})},
            {"label": "Harbor Point Hotel, 2–4 Nov (summit block)", "args": (
                {"hold_id": hotel["hold_id"]} if browser else {"option_id": hotel["option_id"], "travelers": ["maya"],
                                                               "check_in": hotel["check_in"], "check_out": hotel["check_out"]})},
            {"label": "Skyway SK431, Wed 4 Nov 18:10 Chicago → Denver, economy", "args": (
                {"hold_id": back["hold_id"]} if browser else {"option_id": back["option_id"], "travelers": ["maya"]})},
        ]
        held = " I've held each one on the booking sites; nothing is booked yet." if browser else ""
        return ("Here's the plan for the summit:\n\n"
                "- **Out:** Skyway SK412, Mon 2 Nov, 15:10 Denver → 18:35 Chicago, economy, $286\n"
                "- **Hotel:** Harbor Point, 2 nights at the summit block rate ($239 a night), $478\n"
                "- **Back:** Skyway SK431, Wed 4 Nov, 18:10 Chicago → 19:55 Denver, economy, $264\n\n"
                f"Total **$1,028**, inside the $1,500 trip cap and the $260 hotel cap.{held} The business fare is out "
                "of policy, and the earlier flight back leaves before the summit ends.\n\n"
                "Reply yes and I'll book all three.\n\nSTATUS: waiting — reply yes to book these three")

    async def _book_plan(self, text: str) -> str:
        plan, self._plan = self._plan or [], None
        browser = bool(self.config and self.config.browser)
        self._emit("thinking_summary", MAIN, text="Maya said yes to the plan she was shown. Book each item, then "
                   "check each booking.")
        done, failed = [], []
        for item in plan:
            ok, res, _ = await self._tool("travel_book", item["args"], gated=True)
            if not ok:
                failed.append(f"{item['label']} ({res})")
                continue
            await self._tool("check_booking", {"booking_id": res["booking_id"]})
            done.append(f"- {item['label']}: **{res['booking_id']}**, ${res['total_usd']}")
            if browser:
                url = ("http://stays.localhost:8766" if res["kind"] == "hotel" else "http://skyway.localhost:8766") + \
                      f"/booking/{res['booking_id']}"
                await self._browser(MAIN, "navigate_page", url)
                await self._screenshot(MAIN, url, f"confirmed-{res['booking_id']}", f"Confirmed · {res['booking_id']}",
                                       [item["label"], f"Total ${res['total_usd']}", "Status: Confirmed"])
        if not done:
            return "I didn't book anything: " + "; ".join(failed) + "\n\nSTATUS: failed — nothing was booked"
        body = "Booked:\n\n" + "\n".join(done)
        if failed:
            return body + "\n\nNot booked: " + "; ".join(failed) + "\n\nSTATUS: partial — some bookings were declined"
        return body + "\n\nConfirmations are in Maya's world, under bookings.\n\nSTATUS: done"

    async def _demo_all(self, text: str) -> str:
        """One event of every trace type, for checking the UI."""
        self._emit("thinking_summary", MAIN, text="Show one of every trace event type (demo only).")
        await self._skill("web-research")
        await self._tool("clock_now", {})
        await self._tool("web_search", {"query": "midwest builders summit 2026"})
        await self._tool("web_fetch", {"url": "https://midwestbuild.example/2026"})
        did, sub = self._delegate("qa", "Check the connectors and report what's on.")
        self._emit("thinking_summary", sub, text="One call answers this.")
        await self._tool("connector_status", {}, sub)
        self._handoff(did, "qa", "All six connectors report their status.")
        cid = self._id()
        args = {"event_id": "ev-doctor"}
        self._emit("tool_call", MAIN, call_id=cid, tool="calendar_cancel", input=args)
        self._emit("approval_request", MAIN, call_id=cid, tool="calendar_cancel", input=args,
                   summary=approvals.summarize("calendar_cancel", args, self.run_dir))  # type: ignore[arg-type]
        await self._pause(0.6)
        self._emit("approval_response", MAIN, call_id=cid, decision="deny", by="script", reason="Demo only: nothing changes.")
        self._emit("tool_result", MAIN, call_id=cid, tool="calendar_cancel", ok=False, output=None,
                   error="Permission denied: Demo only: nothing changes.")
        await self._tool("check_event", {"event_id": "ev-standup-27"})
        cid = self._id()
        problems = [{"code": "title_too_long", "detail": "Slide 2: title is 74 characters (limit 60)"},
                    {"code": "untraced_number", "detail": "Slide 3: 38% isn't in any source"}]
        self._emit("tool_call", MAIN, call_id=cid, tool="check_deck", input={"deck_id": "dk-demo"})
        self._emit("tool_result", MAIN, call_id=cid, tool="check_deck", ok=True, output={"ok": False, "problems": problems},
                   error="")
        self._emit("check", MAIN, call_id=cid, verifier="check_deck", object_id="dk-demo", ok=False, problems=problems)
        for kind, obj, detail in (("decline", "ev-demo", "Lisa Park declined: I'm out of office"),
                                  ("reply", "em-r1", "Raj Mehta replied: Thursday 2pm works."),
                                  ("travel_flag", "bk-demo", "Business class needs VP approval"),
                                  ("correction", None, "Maya: no, the London office, not New York")):
            self._emit("signal", MAIN, kind=kind, object_id=obj, detail=detail)
            await self._pause(0.2)
        site = "http://tables.localhost:8766"
        await self._browser(MAIN, "navigate_page", site + "/")
        self._site(MAIN, "page_view", site + "/", site="tables")
        await self._browser(MAIN, "fill", site + "/", target="Party size", value="4")
        await self._browser(MAIN, "click", site + "/", target="Find a table")
        self._site(MAIN, "form_submit", site + "/search", site="tables", form="search")
        self._site(MAIN, "hold_created", site + "/review", site="tables", hold_id="HOLD-DEMO", total_usd=340)
        await self._screenshot(MAIN, site + "/review", "tables-review", "Tables · Review HOLD-DEMO",
                               ["Ember & Oak, Tue 3 Nov 19:00", "Party of 4 · about $340", "Held for 30 minutes"])
        self._emit("stop_check_block", MAIN, reason="calendar_create ev-n1 has no check_event after it. Run check_event "
                   "before you finish.")
        self._emit("user", MAIN, text="End your reply with a STATUS line.", source="harness")
        self._emit("budget_exceeded", MAIN, which="seconds")
        self._emit("error", MAIN, where="demo", message="This is what an error looks like.")
        return ("That was one of every trace event type: skills, tools, searches, a hand-off, an approval, checks, "
                "signals, browser steps, a stop-check block, a budget note and an error.\n\n"
                "STATUS: partial — demo only, nothing real happened")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _mock_page_svg(url: str, title: str, lines: list[str]) -> str:
    """A small drawing of a booking page, standing in for a real browser screenshot."""
    rows = "".join(
        f'<rect x="32" y="{150 + i * 58}" width="576" height="44" rx="8" fill="#ffffff" stroke="#d9d2c4"/>'
        f'<text x="48" y="{178 + i * 58}" font-size="16" fill="#2a251f">{_esc(line)}</text>'
        for i, line in enumerate(lines[:4])
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" viewBox="0 0 640 400" '
        'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif">'
        '<rect width="640" height="400" fill="#f4efe6"/>'
        '<rect width="640" height="44" fill="#e7e0d3"/>'
        '<circle cx="20" cy="22" r="6" fill="#c9bfae"/><circle cx="40" cy="22" r="6" fill="#c9bfae"/>'
        '<circle cx="60" cy="22" r="6" fill="#c9bfae"/>'
        '<rect x="84" y="11" width="530" height="22" rx="11" fill="#fbf8f2"/>'
        f'<text x="98" y="27" font-size="12" fill="#6b6257">{_esc(url[:80])}</text>'
        f'<text x="32" y="96" font-size="24" font-weight="600" fill="#1f1b16">{_esc(title)}</text>'
        '<rect x="32" y="112" width="64" height="4" rx="2" fill="#b4532a"/>'
        f"{rows}</svg>"
    )
