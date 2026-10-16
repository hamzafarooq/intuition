"""`ea-eval estimate`: per-trial figures from the October 2026 setup-baseline run."""

from ea_evals import cli


def estimate(capsys, *argv: str) -> str:
    args = cli.build_parser().parse_args(["estimate", *argv])
    assert args.fn(args) == 0
    return capsys.readouterr().out


def test_setup_baseline_estimate_matches_the_measured_run(capsys) -> None:
    out = estimate(capsys, "--slice", "setup-baseline")
    assert out.startswith("10 trials ≈ $7.54 of Claude plan usage")  # measured: $7.07, with M01 at $1.61
    assert "$1.50 OpenAI judging" in out
    assert "reference run" not in out


def test_per_case_figures() -> None:
    assert cli.trial_estimate({"type": "team"}, {}) == (2.14, cli.EST_SECONDS["team"])
    assert cli.trial_estimate({"type": "booking"}, {"browser": "on"})[0] == 2.09
    assert cli.trial_estimate({"type": "booking"}, {})[0] == 0.60
    assert cli.trial_estimate({"type": "deck"}, {})[0] == 0.71  # not in the baseline: the average


def test_browser_slices_run_one_at_a_time(capsys) -> None:
    out = estimate(capsys, "--slice", "browser-demo")
    assert "--parallel 1" in out


def test_phoenix_without_the_extra_prints_the_hint(capsys, monkeypatch) -> None:
    import sys

    import ea_evals

    monkeypatch.setitem(sys.modules, "ea_evals.phoenix", None)  # as if the extra weren't installed
    monkeypatch.delattr(ea_evals, "phoenix", raising=False)
    args = cli.build_parser().parse_args(["phoenix", "smoke-s01", "--dry-run"])
    assert args.fn(args) == 2
    assert "uv run --extra phoenix ea-eval phoenix smoke-s01" in capsys.readouterr().err
