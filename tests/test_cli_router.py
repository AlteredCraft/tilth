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
    monkeypatch.setattr(loop, "do_push_cmd", make_stub("push"))
    monkeypatch.setattr(loop, "do_pr_cmd", make_stub("pr"))
    monkeypatch.setattr(loop, "do_cleanse_cmd", make_stub("cleanse"))
    monkeypatch.setattr(loop, "do_reset_cmd", make_stub("reset"))
    monkeypatch.setattr(loop, "do_visualize_cmd", make_stub("visualize"))
    monkeypatch.setattr(loop, "do_info_cmd", make_stub("info"))
    monkeypatch.setattr(loop, "do_config_cmd", make_stub("config"))
    return calls


def _run(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["tilth", *argv])
    return cli.main()


def test_no_args_prints_help_and_returns_nonzero(monkeypatch, capsys):
    rc = _run(monkeypatch, [])
    assert rc == 1
    out = capsys.readouterr().out
    assert "usage: tilth" in out
    assert "Config locations" in out


def test_top_level_help_exits_zero(monkeypatch, capsys):
    rc = _run(monkeypatch, ["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "run" in out
    assert "Config locations" in out
    assert "prep-feature" not in out  # removed verb must not resurface in help


def test_help_shows_resolved_paths_when_present(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("TILTH_BASE_URL=https://x/v1\n")
    monkeypatch.setenv("TILTH_HOME", str(home))
    _run(monkeypatch, ["--help"])
    out = capsys.readouterr().out
    assert str(home) in out
    assert str(env_file) in out
    assert "tilth init" not in out


def test_help_suggests_init_when_home_and_env_missing(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("TILTH_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    _run(monkeypatch, ["--help"])
    out = capsys.readouterr().out
    assert str(home) in out
    assert "not found" in out
    assert "tilth init" in out


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


def test_push_with_id_default_remote(monkeypatch, patched_handlers):
    _run(monkeypatch, ["push", "20260525-100000-aaa"])
    assert patched_handlers == [("push", ("20260525-100000-aaa",), {"remote": "origin"})]


def test_push_without_id_custom_remote(monkeypatch, patched_handlers):
    _run(monkeypatch, ["push", "--remote", "upstream"])
    assert patched_handlers == [("push", (None,), {"remote": "upstream"})]


def test_pr_with_id_defaults(monkeypatch, patched_handlers):
    _run(monkeypatch, ["pr", "20260525-100000-aaa"])
    assert patched_handlers == [
        ("pr", ("20260525-100000-aaa",), {"base": None, "remote": "origin", "web": False})
    ]


def test_pr_with_flags(monkeypatch, patched_handlers):
    _run(monkeypatch, ["pr", "--base", "develop", "--remote", "upstream", "--web"])
    assert patched_handlers == [
        ("pr", (None,), {"base": "develop", "remote": "upstream", "web": True})
    ]


def test_cleanse_with_id(monkeypatch, patched_handlers):
    _run(monkeypatch, ["cleanse", "20260525-100000-aaa"])
    assert patched_handlers == [("cleanse", ("20260525-100000-aaa", False), {})]


def test_cleanse_with_id_and_yes(monkeypatch, patched_handlers):
    _run(monkeypatch, ["cleanse", "20260525-100000-aaa", "-y"])
    assert patched_handlers == [("cleanse", ("20260525-100000-aaa", True), {})]


def test_cleanse_without_id_passes_none(monkeypatch, patched_handlers):
    _run(monkeypatch, ["cleanse"])
    assert patched_handlers == [("cleanse", (None, False), {})]


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


def test_info_with_id(monkeypatch, patched_handlers):
    _run(monkeypatch, ["info", "20260525-100000-aaa"])
    assert patched_handlers == [("info", ("20260525-100000-aaa",), {})]


def test_info_without_id_passes_none(monkeypatch, patched_handlers):
    _run(monkeypatch, ["info"])
    assert patched_handlers == [("info", (None,), {})]


def test_config_dispatches(monkeypatch, patched_handlers):
    _run(monkeypatch, ["config"])
    assert patched_handlers == [("config", (), {})]


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
