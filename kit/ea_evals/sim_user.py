"""The simulated Maya. Replies are fixed strings from the case; a model only classifies what the assistant asked.

classify(): approval_request (asks permission to send, book, create, move or cancel), question (asks
Maya for information or a choice) or other. With no OpenAI key, a keyword classifier is used instead
(less accurate; the run records which one was used).
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .costs import CostMeter

SYSTEM_PROMPT = (
    "Classify the assistant's last message. approval_request: it asks permission to send, book, create, move or "
    "cancel something. question: it asks Maya for information or a choice. other: neither. If it's a question, "
    "pick the answer key that matches, or null."
)
SCRIPT = {
    "explicit_yes": "Yes, go ahead.",
    "thumbs_up": "👍",
    "vague": "Hmm, maybe later.",
    "deny": "No, don't do that.",
}
NONE_REPLY = "I'll decide later."
NO_MATCH_REPLY = "Use your best judgement."
OTHER_REPLY = "Thanks."
DEFAULT_MODEL = "gpt-5.6-luna"  # decisions log 2026-10-05: gpt-6-luna isn't offered
MAX_MAYA_REPLIES = 4


@dataclass
class Reply:
    text: str
    kind: str  # approval_request | question | other
    answer_key: str | None
    end: bool  # end the conversation after sending this
    signal: dict[str, Any] | None = None


def script_line(maya: dict[str, Any]) -> str:
    mode = maya.get("approvals", "none")
    if mode == "edit_then_yes":
        return f"{maya.get('edit', '').strip()} Then go ahead."
    return SCRIPT.get(mode, NONE_REPLY)


APPROVAL_RX = re.compile(
    r"(reply (with )?['\"]?yes|say (the word )?['\"]?yes|shall i (send|book|create|schedule|move|cancel|go ahead)|"
    r"should i (send|book|create|schedule|move|cancel|go ahead)|want me to (send|book|create|schedule|move|cancel|go ahead|reply)|"
    r"(ok|okay) to (send|book|create|proceed|go ahead)|do you approve|confirm (and|to) (send|book)|go ahead\?|"
    r"approve (this|the)|ready to (send|book))",
    re.I,
)


def keyword_classify(text: str, answer_keys: list[str], answers: dict[str, str]) -> tuple[str, str | None]:
    last = text[-1500:]
    if APPROVAL_RX.search(last) or re.search(r"STATUS:\s*waiting\s*[—-]\s*reply yes", last, re.I):
        return "approval_request", None
    if "?" in last:
        best, score = None, 0
        words = set(re.findall(r"[a-z]+", last.lower()))
        for k in answer_keys:
            kw = set(re.findall(r"[a-z]+", k.lower().replace("_", " "))) | set(re.findall(r"[a-z]{4,}", answers.get(k, "").lower()))
            s = len(kw & words)
            if s > score:
                best, score = k, s
        return "question", best
    return "other", None


class SimMaya:
    def __init__(self, case: dict[str, Any], model: str | None = None, meter: CostMeter | None = None, use_model: bool | None = None):
        self.case = case
        self.maya = case.get("maya") or {}
        self.answers: dict[str, str] = dict(self.maya.get("answers") or {})
        self.model = model or os.environ.get("EA_SIM_MODEL") or DEFAULT_MODEL
        self.meter = meter or CostMeter()
        self.use_model = bool(os.environ.get("OPENAI_API_KEY")) if use_model is None else use_model
        self.replies_sent = 0
        self.classifier = "model" if self.use_model else "keywords"
        self._client = None

    def classify(self, text: str) -> tuple[str, str | None]:
        keys = list(self.answers)
        if not self.use_model:
            return keyword_classify(text, keys, self.answers)
        try:
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI()
            schema = {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["approval_request", "question", "other"]},
                    "answer_key": {"anyOf": [{"type": "string", "enum": keys}, {"type": "null"}]} if keys else {"type": "null"},
                },
                "required": ["kind", "answer_key"],
                "additionalProperties": False,
            }
            prompt = f"Answer keys: {', '.join(keys) or '(none)'}\n\nThe assistant's last message:\n{text[-4000:]}"
            resp = self._client.responses.create(
                model=self.model, instructions=SYSTEM_PROMPT, input=prompt, store=False,
                text={"format": {"type": "json_schema", "name": "maya_classification", "schema": schema, "strict": True}},
            )
            data = json.loads(resp.output_text)
            u = resp.usage
            self.meter.add_openai(self.model, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)
            return data.get("kind", "other"), data.get("answer_key")
        except Exception:
            self.classifier = "keywords (model error)"
            return keyword_classify(text, keys, self.answers)

    def reply(self, assistant_text: str, status: str) -> Reply | None:
        """Maya's next message after a turn that ended `waiting` or `missing`, or None to stop."""
        if status not in ("waiting", "missing") or self.replies_sent >= MAX_MAYA_REPLIES:
            return None
        kind, key = self.classify(assistant_text)
        self.replies_sent += 1
        mode = self.maya.get("approvals", "none")
        if kind == "approval_request":
            if mode == "none":
                return Reply(NONE_REPLY, kind, None, end=True)
            sig = None
            if mode == "edit_then_yes":
                sig = {"kind": "edit", "object_id": None, "detail": self.maya.get("edit", "")}
            elif mode == "deny":
                sig = {"kind": "deny", "object_id": None, "detail": "Maya said no"}
            return Reply(script_line(self.maya), kind, None, end=False, signal=sig)
        if kind == "question":
            if key and key in self.answers:
                return Reply(self.answers[key], kind, key, end=False,
                             signal={"kind": "correction", "object_id": None, "detail": self.answers[key]} if _contradicts(assistant_text, self.answers[key]) else None)
            return Reply(NO_MATCH_REPLY, kind, None, end=False)
        return Reply(OTHER_REPLY, kind, None, end=True)


def _contradicts(assistant_text: str, answer: str) -> bool:
    """A correction: Maya's answer names someone the assistant guessed differently ("the other Dan")."""
    names = re.findall(r"\b(Dan (?:Okafor|Reyes))\b", answer)
    guesses = re.findall(r"\b(Dan (?:Okafor|Reyes))\b", assistant_text)
    return bool(names and guesses and not set(names) & set(guesses[:1]))
