"""TilthConfig.from_env() must fail fast when required vars are missing.

The required trio is TILTH_BASE_URL, TILTH_API_KEY, TILTH_WORKER_MODEL. We don't
want defaults silently picking a provider/model that may not match the user's
account — that produces confusing 401/404s well after `tilth` starts running.
"""

from __future__ import annotations

import pytest

from tilth.client import TilthConfig

REQUIRED = ("TILTH_BASE_URL", "TILTH_API_KEY", "TILTH_WORKER_MODEL")
OPTIONAL = (
    "TILTH_JUDGE_MODEL",
    "TILTH_JUDGE_BASE_URL",
    "TILTH_JUDGE_API_KEY",
    "TILTH_REASONING_ENABLED",
)


def _clear(monkeypatch) -> None:
    for name in (*REQUIRED, *OPTIONAL):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("missing", REQUIRED)
def test_from_env_raises_when_required_missing(monkeypatch, missing):
    _clear(monkeypatch)
    for name in REQUIRED:
        if name != missing:
            monkeypatch.setenv(name, "x")
    with pytest.raises(RuntimeError) as excinfo:
        TilthConfig.from_env()
    assert missing in str(excinfo.value)


def test_from_env_lists_all_missing_vars(monkeypatch):
    _clear(monkeypatch)
    with pytest.raises(RuntimeError) as excinfo:
        TilthConfig.from_env()
    msg = str(excinfo.value)
    for name in REQUIRED:
        assert name in msg


def test_from_env_succeeds_with_required_set(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TILTH_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setenv("TILTH_API_KEY", "k")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "test-model")
    cfg = TilthConfig.from_env()
    assert cfg.base_url == "https://test.invalid/v1"
    assert cfg.api_key == "k"
    assert cfg.worker_model == "test-model"
    assert cfg.judge_model == "test-model"
    assert cfg.judge_base_url == "https://test.invalid/v1"
    assert cfg.judge_api_key == "k"
