"""tilth.cli.main routes subcommands to the right loop handlers, and
back-compat shims the pre-Phase-3 surface (bare positional + legacy flags).

We don't exercise the handlers themselves here — those have dedicated tests.
This file pins the routing contract: given an argv, the right thing runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tilth import cli, loop


@pytest.fixture
def patched_handlers(monkeypatch):
    """Replace each subcommand handler with a stub that records its call."""
    calls: list[tuple[str, tuple]] = []

    def make_stub(name: str):
        def stub(*args):
            calls.append((name, args))
            return 0
        return stub

    monkeypatch.setattr(loop, "do_prep_feature_cmd", make_stub("prep-feature"))
    monkeypatch.setattr(loop, "do_run_cmd", make_stub("run"))
    monkeypatch.setattr(loop, "do_resume_cmd", make_stub("resume"))
    monkeypatch.setattr(loop, "do_reset_cmd", make_stub("reset"))
    monkeypatch.setattr(loop, "do_visualize_cmd", make_stub("visualize"))
    monkeypatch.setattr(loop, "_legacy_main", make_stub("legacy"))
    return calls


def _run(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["tilth", *argv])
    return cli.main()


def test_no_args_prints_help_and_returns_nonzero(monkeypatch, capsys):
    rc = _run(monkeypatch, [])
    assert rc == 1
    out = capsys.readouterr().out
    assert "usage: tilth" in out


def test_top_level_help_exits_zero(monkeypatch, capsys):
    rc = _run(monkeypatch, ["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "prep-feature" in out
    assert "run" in out


def test_run_subcommand_dispatches_to_do_run_cmd(monkeypatch, patched_handlers):
    rc = _run(monkeypatch, ["run", "/tmp/some-repo"])
    assert rc == 0
    assert patched_handlers == [("run", (Path("/tmp/some-repo"),))]


def test_prep_feature_dispatches_with_workspace_and_brief(monkeypatch, patched_handlers):
    rc = _run(monkeypatch, ["prep-feature", "/tmp/repo", "--brief", "add X"])
    assert rc == 0
    assert patched_handlers == [("prep-feature", (Path("/tmp/repo"), "add X"))]


def test_prep_feature_brief_defaults_to_none(monkeypatch, patched_handlers):
    rc = _run(monkeypatch, ["prep-feature", "/tmp/repo"])
    assert rc == 0
    assert patched_handlers == [("prep-feature", (Path("/tmp/repo"), None))]


def test_resume_with_id(monkeypatch, patched_handlers):
    _run(monkeypatch, ["resume", "20260525-100000-aaa"])
    assert patched_handlers == [("resume", ("20260525-100000-aaa",))]


def test_resume_without_id_passes_none(monkeypatch, patched_handlers):
    _run(monkeypatch, ["resume"])
    assert patched_handlers == [("resume", (None,))]


def test_reset_with_id_and_yes(monkeypatch, patched_handlers):
    _run(monkeypatch, ["reset", "20260525-100000-aaa", "-y"])
    assert patched_handlers == [("reset", ("20260525-100000-aaa", True))]


def test_reset_without_id_or_yes(monkeypatch, patched_handlers):
    _run(monkeypatch, ["reset"])
    assert patched_handlers == [("reset", (None, False))]


def test_visualize_with_id(monkeypatch, patched_handlers):
    _run(monkeypatch, ["visualize", "20260525-100000-aaa"])
    assert patched_handlers == [("visualize", ("20260525-100000-aaa",))]


def test_visualize_without_id(monkeypatch, patched_handlers):
    _run(monkeypatch, ["visualize"])
    assert patched_handlers == [("visualize", (None,))]


# --- back-compat -----------------------------------------------------------

def test_bare_positional_workspace_routes_to_legacy_with_deprecation(
    monkeypatch, patched_handlers, capsys
):
    """The proposal §5.5 specifies: bare `tilth <ws>` emits a deprecation
    pointer, then dispatches the legacy main (which continues as `run`)."""
    rc = _run(monkeypatch, ["/tmp/some-repo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deprecated" in out
    assert "tilth run /tmp/some-repo" in out
    assert patched_handlers == [("legacy", ())]


def test_legacy_flag_routes_to_legacy_without_deprecation(
    monkeypatch, patched_handlers, capsys
):
    """The old --resume/--reset/--visualize/--prep-feature flags keep working
    silently (no deprecation noise) for one minor version. Their dispatch
    goes through the legacy main."""
    rc = _run(monkeypatch, ["--resume"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deprecated" not in out
    assert patched_handlers == [("legacy", ())]


def test_loop_main_shim_still_callable(monkeypatch, patched_handlers):
    """Anyone importing `from tilth.loop import main` for the entry point
    must still get a working dispatch path."""
    monkeypatch.setattr(sys, "argv", ["tilth", "run", "/tmp/x"])
    rc = loop.main()
    assert rc == 0
    assert patched_handlers == [("run", (Path("/tmp/x"),))]


# --- error surface ---------------------------------------------------------

def test_unknown_subcommand_treated_as_bare_positional(monkeypatch, patched_handlers, capsys):
    """Argparse subparsers would error on an unknown subcommand; we route
    unknown first-tokens to the legacy path so a typo doesn't crash on the
    subcommand parser before showing the deprecation pointer."""
    rc = _run(monkeypatch, ["totally-not-a-subcommand"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deprecated" in out
    assert patched_handlers == [("legacy", ())]
