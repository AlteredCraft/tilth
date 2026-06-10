"""tilth.cli.main routes subcommands to the right loop handlers.

We don't exercise the handlers themselves here — those have dedicated tests.
This file pins the routing contract: given an argv, the right thing runs. The
prompt-driven refactor dropped prep-feature and the legacy flag/bare-positional
surface; the verbs are run / resume / reset / visualize.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tilth import cli, loop


@pytest.fixture
def patched_handlers(monkeypatch):
    """Replace each subcommand handler with a stub that records its call."""
    calls: list[tuple[str, tuple, dict]] = []

    def make_stub(name: str):
        def stub(*args, **kwargs):
            calls.append((name, args, kwargs))
            return 0
        return stub

    monkeypatch.setattr(loop, "do_run_cmd", make_stub("run"))
    monkeypatch.setattr(loop, "do_resume_cmd", make_stub("resume"))
    monkeypatch.setattr(loop, "do_reset_cmd", make_stub("reset"))
    monkeypatch.setattr(loop, "do_visualize_cmd", make_stub("visualize"))
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
    assert "run" in out
    assert "prep-feature" not in out  # removed verb must not resurface in help


def test_run_subcommand_dispatches_to_do_run_cmd(monkeypatch, patched_handlers):
    rc = _run(monkeypatch, ["run", "/tmp/some-repo"])
    assert rc == 0
    assert patched_handlers == [("run", (Path("/tmp/some-repo"),), {})]


def test_resume_with_id(monkeypatch, patched_handlers):
    _run(monkeypatch, ["resume", "20260525-100000-aaa"])
    assert patched_handlers == [("resume", ("20260525-100000-aaa",), {})]


def test_resume_without_id_passes_none(monkeypatch, patched_handlers):
    _run(monkeypatch, ["resume"])
    assert patched_handlers == [("resume", (None,), {})]


def test_reset_with_id_and_yes(monkeypatch, patched_handlers):
    _run(monkeypatch, ["reset", "20260525-100000-aaa", "-y"])
    assert patched_handlers == [("reset", ("20260525-100000-aaa", True), {})]


def test_reset_without_id_or_yes(monkeypatch, patched_handlers):
    _run(monkeypatch, ["reset"])
    assert patched_handlers == [("reset", (None, False), {})]


def test_visualize_with_id(monkeypatch, patched_handlers):
    _run(monkeypatch, ["visualize", "20260525-100000-aaa"])
    assert patched_handlers == [
        ("visualize", ("20260525-100000-aaa",), {"port": 8765})
    ]


def test_visualize_without_id(monkeypatch, patched_handlers):
    _run(monkeypatch, ["visualize"])
    assert patched_handlers == [("visualize", (None,), {"port": 8765})]


def test_visualize_custom_port(monkeypatch, patched_handlers):
    _run(monkeypatch, ["visualize", "--port", "9000"])
    assert patched_handlers == [("visualize", (None,), {"port": 9000})]


def test_loop_main_shim_still_callable(monkeypatch, patched_handlers):
    """`from tilth.loop import main` must still get a working dispatch path."""
    monkeypatch.setattr(sys, "argv", ["tilth", "run", "/tmp/x"])
    rc = loop.main()
    assert rc == 0
    assert patched_handlers == [("run", (Path("/tmp/x"),), {})]


def test_unknown_subcommand_errors(monkeypatch):
    """An unknown first token is an argparse usage error (exit 2), not a silent
    fall-through — there's no legacy surface to route it to anymore."""
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["totally-not-a-subcommand"])
    assert exc.value.code == 2
