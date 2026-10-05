"""Claude Code headless adapter: runs `claude -p` turn by turn and turns stream-json into the trace.

One conversation per trial: the first turn's session id is resumed on every later turn. Approvals go
through ea-world's `approval_prompt` tool (--permission-prompt-tool). The tool server's call log,
approvals log, stop-check log and the booking sites' log are merged into the trace live, so the app
can show approval cards while Claude Code waits.
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ea_world import state as world_state

from .base import RunConfig, TurnResult, Usage
from .overlay import allowed_tools, mcp_config
from .trace import TraceWriter, parse_status, truncate

EA_PREFIX = "mcp__ea-world__"
BROWSER_PREFIX = "mcp__browser__"
PLUMBING = {"ToolSearch", "TodoWrite"}
WRITE_TOOLS = {"calendar_create", "calendar_update", "calendar_cancel", "email_send", "travel_book",
               "travel_cancel", "restaurant_book", "brief_save", "deck_create", "connector_disconnect",
               "email_draft", "email_update_draft"}  # fmt: skip
USAGE_LIMIT_RX = re.compile(r"(usage limit|limit reached|rate limit|resets at|out of (extra )?usage|5-hour limit|weekly limit)", re.I)
EXTRA_ENV = {
    "MCP_TOOL_TIMEOUT": "600000",
    "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS": "900000",
    "MCP_TIMEOUT": "60000",
}
TOOL_ENV_KEYS = ["EA_MODE", "EA_WORLD_VARIANT", "EA_FAULTS", "EA_SEED", "EA_APPROVAL_MODE", "EA_IDEMPOTENCY",
                 "SERPAPI_API_KEY", "PATH", "HOME", "EA_KIT_ROOT", "EA_LOG_LEVEL", "EA_WRITE_CURRENT"]  # fmt: skip


def claude_binary() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if cand.exists():
            return str(cand)
    return None


class _Tail:
    """Reads new lines appended to a JSONL file."""

    def __init__(self, path: Path):
        self.path = path
        self.pos = 0
        self.buf = ""

    def skip_existing(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.pos = self.path.stat().st_size

    def read(self) -> list[dict[str, Any]]:
        try:
            with open(self.path, encoding="utf-8") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
        except FileNotFoundError:
            return []
        self.buf += chunk
        lines = self.buf.split("\n")
        self.buf = lines.pop()
        out = []
        for line in lines:
            line = line.strip()
            if line:
                with contextlib.suppress(ValueError):
                    out.append(json.loads(line))
        return out


class StreamMapper:
    """Maps Claude Code stream-json events (one turn) to trace events. Usable offline on recordings."""

    def __init__(self, trace: TraceWriter, run_dir: Path):
        self.trace = trace
        self.run_dir = run_dir
        self.agent_of: dict[str, str] = {}      # Agent tool_use id -> sub-agent name
        self.tool_of: dict[str, tuple[str, str, dict[str, Any]]] = {}  # tool_use id -> (kind, bare name, input)
        self.calls = _Tail(run_dir / "calls.jsonl")
        self.pending_calls: list[dict[str, Any]] = []
        self.session_id: str | None = None
        self.init: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None
        self.texts: list[str] = []
        self.usage_limit = False
        self.errors: list[str] = []

    # -- helpers ------------------------------------------------------------
    def _agent(self, ev: dict[str, Any]) -> tuple[str, str | None]:
        parent = ev.get("parent_tool_use_id")
        if parent:
            return self.agent_of.get(parent, "subagent"), parent
        return "main", None

    def _match_call(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        self.pending_calls += self.calls.read()
        for i, c in enumerate(self.pending_calls):
            if c.get("tool") == tool and _same_args(c.get("args", {}), args):
                return self.pending_calls.pop(i)
        for i, c in enumerate(self.pending_calls):
            if c.get("tool") == tool:
                return self.pending_calls.pop(i)
        return None

    # -- mapping ------------------------------------------------------------
    def feed(self, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        if ev.get("session_id"):
            self.session_id = ev["session_id"]
        if t == "system":
            if ev.get("subtype") == "init":
                self.init = ev
            return
        if t == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            if info.get("status") not in (None, "allowed", "allowed_warning"):
                self.usage_limit = True
            return
        if t == "assistant":
            self._assistant(ev)
        elif t == "user":
            self._user(ev)
        elif t == "result":
            self.result = ev

    def _assistant(self, ev: dict[str, Any]) -> None:
        agent, parent = self._agent(ev)
        content = (ev.get("message") or {}).get("content") or []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for b in content:
            bt = b.get("type")
            if bt == "text" and b.get("text", "").strip():
                if agent == "main":
                    self.texts.append(b["text"])
                self.trace.emit("assistant", agent=agent, parent_call_id=parent, text=b["text"])
            elif bt == "thinking" and (b.get("thinking") or "").strip():
                self.trace.emit("thinking_summary", agent=agent, parent_call_id=parent, text=b["thinking"])
            elif bt == "tool_use":
                self._tool_use(b, agent, parent)

    def _tool_use(self, b: dict[str, Any], agent: str, parent: str | None) -> None:
        name, cid, inp = b.get("name", ""), b.get("id", ""), b.get("input") or {}
        if name in PLUMBING:
            self.tool_of[cid] = ("plumbing", name, inp)
            return
        if name == "Skill":
            self.tool_of[cid] = ("skill", inp.get("skill", ""), inp)
            self.trace.emit("skill_loaded", agent=agent, parent_call_id=parent, call_id=cid, skill=inp.get("skill") or inp.get("command", ""))
            return
        if name in ("Agent", "Task"):
            to = inp.get("subagent_type") or inp.get("agent") or "general-purpose"
            self.agent_of[cid] = to
            self.tool_of[cid] = ("agent", to, inp)
            self.trace.emit("delegate", agent=agent, parent_call_id=parent, call_id=cid, to=to,
                            task=inp.get("prompt", ""), description=inp.get("description", ""))
            return
        if name.startswith(EA_PREFIX):
            bare = name[len(EA_PREFIX):]
            self.tool_of[cid] = ("ea", bare, inp)
            self.trace.emit("tool_call", agent=agent, parent_call_id=parent, call_id=cid, tool=bare, input=inp)
            return
        if name.startswith(BROWSER_PREFIX):
            bare = name[len(BROWSER_PREFIX):]
            self.tool_of[cid] = ("browser", bare, inp)
            self.trace.emit("tool_call", agent=agent, parent_call_id=parent, call_id=cid, tool=f"browser.{bare}", input=inp, server="browser")
            self.trace.emit("browser_step", agent=agent, parent_call_id=parent, call_id=cid, action=bare,
                            url=inp.get("url", ""), target=inp.get("uid", ""), value=inp.get("value", ""), detail=truncate(inp, 300))
            return
        self.tool_of[cid] = ("builtin", name, inp)
        self.trace.emit("tool_call", agent=agent, parent_call_id=parent, call_id=cid, tool=name, input=inp, builtin=True)

    def _user(self, ev: dict[str, Any]) -> None:
        agent, parent = self._agent(ev)
        content = (ev.get("message") or {}).get("content") or []
        if isinstance(content, str):
            return
        for b in content:
            if b.get("type") != "tool_result":
                continue
            cid = b.get("tool_use_id", "")
            kind, name, inp = self.tool_of.get(cid, ("builtin", "?", {}))
            text = _result_text(b.get("content"))
            is_error = bool(b.get("is_error"))
            if kind in ("plumbing", "skill"):
                continue
            parsed = _maybe_json(text)
            if kind == "agent":
                self.trace.emit("tool_result", agent=agent, parent_call_id=parent, call_id=cid, tool="Agent", ok=not is_error,
                                output=truncate(text), error=text if is_error else "")
                self.trace.emit("handoff_result", agent=agent, parent_call_id=parent, call_id=cid, to=name, text=_strip_handback(text))
                continue
            tool_label = name if kind != "browser" else f"browser.{name}"
            err = _clean_error(text) if is_error else ""
            self.trace.emit("tool_result", agent=agent, parent_call_id=parent, call_id=cid, tool=tool_label, ok=not is_error,
                            output=truncate(parsed if parsed is not None else text), error=err)
            if kind == "browser" and name == "take_screenshot":
                self.trace.emit("browser_step", agent=agent, parent_call_id=parent, call_id=cid, action="screenshot",
                                url="", detail=inp.get("filePath", ""), path=inp.get("filePath", ""))
            if kind != "ea":
                continue
            denied = is_error and ("denied" in text.lower() or "hasn't approved" in text.lower() or "permission" in text.lower())
            record = None if denied else self._match_call(name, inp)
            if name.startswith("check_") and not is_error and isinstance(parsed, dict):
                self.trace.emit("check", agent=agent, parent_call_id=parent, call_id=cid, verifier=name,
                                object_id=(record or {}).get("object_id") or next(iter(inp.values()), None),
                                ok=bool(parsed.get("ok")), problems=parsed.get("problems", []))
            for sig in (record or {}).get("signals") or []:
                self.trace.emit("signal", agent=agent, parent_call_id=parent, kind=sig.get("kind"),
                                object_id=sig.get("object_id"), detail=sig.get("detail", ""), call_id=cid)


def _same_args(a: dict[str, Any], b: dict[str, Any]) -> bool:
    strip = lambda d: {k: v for k, v in (d or {}).items() if v is not None and k != "idempotency_key"}  # noqa: E731
    return json.dumps(strip(a), sort_keys=True, default=str) == json.dumps(strip(b), sort_keys=True, default=str)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return json.dumps(content, default=str) if content is not None else ""


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _clean_error(text: str) -> str:
    m = re.search(r"<tool_use_error>(.*?)</tool_use_error>", text, re.S)
    return (m.group(1) if m else text).strip()


def _strip_handback(text: str) -> str:
    """Claude Code wraps sub-agent reports in a hand-back preamble; keep the report."""
    if text.startswith("[Subagent hand-back]"):
        lines = text.split("\n")
        body = [ln[2:] if ln.startswith("  ") else ln for ln in lines[1:]]
        return "\n".join(body).strip()
    return text


class ClaudeCodeHarness:
    """The Harness interface over the Claude Code CLI."""

    def __init__(self) -> None:
        self.run_dir: Path | None = None
        self.config: RunConfig | None = None
        self.trace: TraceWriter | None = None
        self.session_id: str | None = None
        self.mcp_path: Path | None = None
        self.tails: dict[str, _Tail] = {}
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self, run_dir: Path, config: RunConfig) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.config = config
        env = {k: v for k, v in config.env.items() if v is not None}
        env.setdefault("EA_RUN_DIR", str(self.run_dir))
        env.setdefault("EA_APPROVAL_MODE", config.approval_mode)
        env.setdefault("EA_IDEMPOTENCY", config.idempotency)
        env.setdefault("EA_MODE", "mock")
        for key in TOOL_ENV_KEYS:
            if key not in env and os.environ.get(key):
                env[key] = os.environ[key]
        self.tool_env = env
        world_state.init_run_dir(self.run_dir, env.get("EA_WORLD_VARIANT"))
        self.mcp_path = self.run_dir / "mcp.json"
        self.mcp_path.write_text(json.dumps(mcp_config(self.run_dir, env, config.browser), indent=2), encoding="utf-8")
        self.trace = TraceWriter(self.run_dir / "trace.jsonl", config.trace_fields, config.on_event)
        for name in ("approvals", "stopcheck", "site", "browser"):
            self.tails[name] = _Tail(self.run_dir / f"{name}.jsonl")
            self.tails[name].skip_existing()

    def command(self, message: str) -> list[str]:
        assert self.config and self.mcp_path
        binary = claude_binary() or "claude"
        cmd = [
            binary, "-p", message,
            "--output-format", "stream-json", "--verbose",
            "--model", self.config.model,
            "--max-turns", str(self.config.max_turns),
            "--mcp-config", str(self.mcp_path), "--strict-mcp-config",
            "--permission-prompt-tool", f"{EA_PREFIX}approval_prompt",
            "--setting-sources", "project,local",
            "--include-hook-events",
            "--allowedTools", *allowed_tools(self.config.browser),
        ]  # fmt: skip
        if self.config.effort:
            cmd += ["--effort", self.config.effort]
        if self.config.browser:
            cmd += ["--append-system-prompt",
                    f"Browser mode is on. Save browser screenshots under {self.shots_dir()}/ "
                    "(pass that folder to the travel specialist)."]
        cmd += list(self.config.extra_args)
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def shots_dir(self) -> Path:
        """Where the browser saves screenshots: inside the working directory, the browser MCP's only writable root."""
        assert self.config
        d = Path(self.config.assistant_dir) / "outputs" / "browser"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _collect_screenshots(self) -> None:
        """Move new screenshots into <run_dir>/outputs/browser/, where the app and the report look."""
        if not (self.config and self.config.browser and self.run_dir):
            return
        src = Path(self.config.assistant_dir) / "outputs" / "browser"
        if not src.exists():
            return
        dst = self.run_dir / "outputs" / "browser"
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file() and time.time() - f.stat().st_mtime > 0.5:  # finished writing
                target = dst / f.name
                with contextlib.suppress(OSError):
                    shutil.move(str(f), str(target))
                    assert self.trace
                    self.trace.emit("browser_step", action="screenshot_saved", url="", detail=f.name, path=f"outputs/browser/{f.name}")

    def _merge_logs(self) -> None:
        assert self.trace
        self._collect_screenshots()
        for rec in self.tails["approvals"].read():
            if rec.get("kind") == "request":
                self.trace.emit("approval_request", call_id=rec.get("call_id"), tool=rec.get("tool"),
                                input=rec.get("input"), summary=rec.get("summary", ""))
            elif rec.get("kind") == "response":
                self.trace.emit("approval_response", call_id=rec.get("call_id"), decision=rec.get("decision"),
                                by=rec.get("by"), reason=rec.get("reason", ""))
                if rec.get("decision") == "deny" and rec.get("by") in ("human", "app"):
                    self.trace.emit("signal", kind="deny", object_id=rec.get("call_id"), detail=rec.get("reason", ""))
        for rec in self.tails["stopcheck"].read():
            self.trace.emit("stop_check_block", agent=rec.get("agent", "main") if rec.get("hook") == "subagent" else "main",
                            reason=rec.get("reason", ""))
        for rec in self.tails["site"].read():
            data = rec.get("data") or {}
            self.trace.emit("site_event", kind=rec.get("type"), site=rec.get("site"), path=rec.get("path"),
                            url=data.get("url", ""), hold_id=data.get("hold_id"), total_usd=data.get("total_usd"), data=data)
        for rec in self.tails["browser"].read():
            if rec.get("decision") == "deny":
                self.trace.emit("browser_step", action="blocked", url=rec.get("url", ""), detail=rec.get("reason", ""))

    async def send(self, user_message: str, source: str = "maya") -> TurnResult:
        assert self.run_dir and self.config and self.trace
        trace = self.trace
        trace.turn += 1
        trace.emit("user", text=user_message, source=source)
        # The stop hook only looks at calls made in this turn.
        calls = world_state.read_jsonl(self.run_dir / "calls.jsonl")
        (self.run_dir / "turn_start").write_text(str(max([c.get("seq", 0) for c in calls] or [0])), encoding="utf-8")
        mapper = StreamMapper(trace, self.run_dir)
        mapper.calls.skip_existing()
        env = {**os.environ, **EXTRA_ENV, "EA_RUN_DIR": str(self.run_dir)}
        if os.environ.get("EA_USE_API_KEY") != "1":
            env.pop("ANTHROPIC_API_KEY", None)  # run on the person's Claude login, not an API key
        started = time.monotonic()
        binary = claude_binary()
        if binary is None:
            trace.emit("error", where="harness", message="claude isn't on the PATH. Install Claude Code and log in.")
            trace.emit("status", status="missing", reason="")
            trace.emit("turn_end", stopped_by="error")
            return TurnResult("", "missing", "", Usage(), "error", "claude not found")
        stopped_by = "end_turn"
        err = ""
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.command(user_message), cwd=str(self.config.assistant_dir), env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=16 * 1024 * 1024,
            )
            stderr_task = asyncio.create_task(self.proc.stderr.read())  # type: ignore[union-attr]

            async def pump() -> None:
                assert self.proc and self.proc.stdout
                while True:
                    line = await self.proc.stdout.readline()
                    if not line:
                        break
                    self._merge_logs()
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    _record_raw(self.run_dir, ev)
                    mapper.feed(ev)
                    self._merge_logs()

            async def watch_logs() -> None:
                while True:
                    await asyncio.sleep(0.3)
                    self._merge_logs()

            watcher = asyncio.create_task(watch_logs())
            try:
                await asyncio.wait_for(pump(), timeout=self.config.turn_timeout_s)
                await asyncio.wait_for(self.proc.wait(), timeout=30)
            except TimeoutError:
                stopped_by, err = "error", f"Turn timed out after {int(self.config.turn_timeout_s)} s"
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
            finally:
                watcher.cancel()
            stderr = (await stderr_task).decode("utf-8", "replace")
            if stderr.strip():
                (self.run_dir / "claude_stderr.log").open("a", encoding="utf-8").write(stderr)
        except Exception as exc:  # never crash the runner
            stopped_by, err = "error", f"Couldn't run claude: {exc}"
        await asyncio.sleep(0.6)
        self._merge_logs()
        elapsed = time.monotonic() - started
        return self._finish(mapper, stopped_by, err, elapsed)

    def _finish(self, mapper: StreamMapper, stopped_by: str, err: str, elapsed: float) -> TurnResult:
        assert self.trace and self.run_dir
        trace = self.trace
        if mapper.init is not None:
            servers = {s.get("name"): s.get("status") for s in mapper.init.get("mcp_servers", [])}
            world_state.update_meta(self.run_dir, claude={
                "session_id": mapper.session_id, "model": mapper.init.get("model"),
                "claude_code_version": mapper.init.get("claude_code_version"), "mcp_servers": servers,
                "agents": mapper.init.get("agents"), "skills": mapper.init.get("skills"),
            })
            if servers.get("ea-world") != "connected":
                err = err or f"ea-world MCP server isn't connected (status: {servers.get('ea-world')})"
                stopped_by = "error"
        if mapper.session_id:
            self.session_id = mapper.session_id
        res = mapper.result or {}
        final_text = res.get("result") if isinstance(res.get("result"), str) else ("\n".join(mapper.texts[-1:]) if mapper.texts else "")
        u = res.get("usage") or {}
        usage = Usage(
            input_tokens=int(u.get("input_tokens", 0) or 0),
            output_tokens=int(u.get("output_tokens", 0) or 0),
            cache_read_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
            api_equivalent_cost_usd=float(res.get("total_cost_usd", 0.0) or 0.0),
            seconds=round(float(res.get("duration_ms", elapsed * 1000) or elapsed * 1000) / 1000, 2),
            num_turns=int(res.get("num_turns", 0) or 0),
        )
        subtype = res.get("subtype", "")
        if res.get("is_error") or subtype.startswith("error"):
            if subtype == "error_max_turns":
                stopped_by = "max_turns"
            elif stopped_by == "end_turn":
                stopped_by = "error"
            err = err or str(res.get("result") or subtype or "error")
        blob = (final_text or "") + " " + err
        if mapper.usage_limit or (stopped_by == "error" and USAGE_LIMIT_RX.search(blob)):
            stopped_by = "usage_limit"
        if err:
            trace.emit("error", where="claude-code", message=err[:2000])
        status, reason = parse_status(final_text)
        models = list((res.get("modelUsage") or {}).keys())
        trace.emit("usage", model=(mapper.init or {}).get("model") or (models[0] if models else ""),
                   input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                   cache_read_tokens=usage.cache_read_tokens, cache_write_tokens=usage.cache_write_tokens,
                   cost_usd=round(usage.api_equivalent_cost_usd, 6), seconds=usage.seconds, num_turns=usage.num_turns)
        trace.emit("status", status=status, reason=reason)
        trace.emit("turn_end", stopped_by=stopped_by)
        return TurnResult(final_text or "", status, reason, usage, stopped_by, err)  # type: ignore[arg-type]

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()


def _record_raw(run_dir: Path, ev: dict[str, Any]) -> None:
    """Keep the raw stream for debugging and for recording offline test fixtures."""
    with open(run_dir / "stream.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def replay(stream: Iterable[dict[str, Any]], run_dir: Path, trace: TraceWriter) -> StreamMapper:
    """Map a recorded stream offline (tests)."""
    mapper = StreamMapper(trace, run_dir)
    for ev in stream:
        mapper.feed(ev)
    return mapper
