"""The .env loader: values reach the environment, and common misspellings of key names still work."""

import os
from pathlib import Path

import pytest

from ea_evals.env import load_dotenv


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("EA_DOTENV", str(p))
    for k in ("OPENAI_API_KEY", "SERPAPI_API_KEY", "SERP_API_KEY", "SERPAPI_KEY"):
        monkeypatch.delenv(k, raising=False)
    return p


def test_reads_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _env(tmp_path, monkeypatch, "# comment\nOPENAI_API_KEY='sk-test'\nexport SERPAPI_API_KEY=serp-test\n")
    assert load_dotenv() == p
    assert os.environ["OPENAI_API_KEY"] == "sk-test"
    assert os.environ["SERPAPI_API_KEY"] == "serp-test"


@pytest.mark.parametrize("name", ["SERP_API_KEY", "SERPAPI_KEY"])
def test_serpapi_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    _env(tmp_path, monkeypatch, f"{name}=serp-alias\n")
    load_dotenv()
    assert os.environ["SERPAPI_API_KEY"] == "serp-alias"


def test_alias_never_overrides_the_real_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(tmp_path, monkeypatch, "SERPAPI_API_KEY=real\nSERP_API_KEY=alias\n")
    load_dotenv()
    assert os.environ["SERPAPI_API_KEY"] == "real"
