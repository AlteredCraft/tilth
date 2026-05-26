"""FileSeedSink validates the seed bundle and writes it atomically.

The validation surface is the contract enforcement the prompt can't enforce:
a model that produces a misshapen seed gets a clean error rather than a
broken half-written state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.seed.sink import FileSeedSink, SeedWriteError


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions" / "20260525-120000-abc123"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _good_entry(idx: int = 1) -> dict:
    return {
        "id": f"T-{idx:03d}",
        "title": f"task {idx}",
        "description": f"do task {idx}",
        "acceptance_criteria": [f"criterion {idx}.1", f"criterion {idx}.2"],
    }


def _good_test(idx: int = 1) -> tuple[str, str]:
    return (
        f"test_t{idx:03d}_slug.py",
        f'"""acceptance for T-{idx:03d}."""\n\ndef test_thing():\n    assert True\n',
    )


def _meta() -> dict:
    return {"interviewer_model": "stub", "tokens": {"total": 0}}


def test_happy_path_writes_prd_meta_and_test_files(session_dir, workspace):
    fname, content = _good_test(1)
    FileSeedSink().write_seed(
        session_dir=session_dir,
        workspace=workspace,
        prd_entries=[_good_entry(1)],
        test_files={fname: content},
        meta=_meta(),
    )
    prd = json.loads((session_dir / "prd.json").read_text())
    assert prd[0]["id"] == "T-001"
    assert prd[0]["status"] == "pending"
    meta = json.loads((session_dir / "seed-meta.json").read_text())
    assert meta == _meta()
    assert (workspace / "tests" / fname).read_text() == content


def test_status_field_is_normalised_to_pending_even_if_model_supplies_done(
    session_dir, workspace
):
    entry = {**_good_entry(1), "status": "done"}
    fname, content = _good_test(1)
    FileSeedSink().write_seed(
        session_dir=session_dir,
        workspace=workspace,
        prd_entries=[entry],
        test_files={fname: content},
        meta=_meta(),
    )
    prd = json.loads((session_dir / "prd.json").read_text())
    assert prd[0]["status"] == "pending"


def test_extra_keys_on_prd_entries_are_dropped(session_dir, workspace):
    entry = {**_good_entry(1), "extra": "ignored"}
    fname, content = _good_test(1)
    FileSeedSink().write_seed(
        session_dir=session_dir,
        workspace=workspace,
        prd_entries=[entry],
        test_files={fname: content},
        meta=_meta(),
    )
    prd = json.loads((session_dir / "prd.json").read_text())
    assert "extra" not in prd[0]
    assert set(prd[0]) == {"id", "title", "description", "acceptance_criteria", "status"}


def test_creates_tests_dir_if_missing(session_dir, workspace):
    assert not (workspace / "tests").exists()
    fname, content = _good_test(1)
    FileSeedSink().write_seed(
        session_dir=session_dir,
        workspace=workspace,
        prd_entries=[_good_entry(1)],
        test_files={fname: content},
        meta=_meta(),
    )
    assert (workspace / "tests").is_dir()


def test_rejects_empty_prd_entries(session_dir, workspace):
    with pytest.raises(SeedWriteError, match="prd_entries is empty"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[],
            test_files={},
            meta=_meta(),
        )


def test_rejects_empty_test_files(session_dir, workspace):
    with pytest.raises(SeedWriteError, match="test_files is empty"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[_good_entry(1)],
            test_files={},
            meta=_meta(),
        )


def test_rejects_missing_required_key(session_dir, workspace):
    entry = _good_entry(1)
    del entry["acceptance_criteria"]
    fname, content = _good_test(1)
    with pytest.raises(SeedWriteError, match="missing required key"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[entry],
            test_files={fname: content},
            meta=_meta(),
        )


@pytest.mark.parametrize("bad_id", ["T-1", "t-001", "T_001", "001", "T-abc"])
def test_rejects_malformed_task_id(session_dir, workspace, bad_id):
    entry = {**_good_entry(1), "id": bad_id}
    with pytest.raises(SeedWriteError, match="task id must match"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[entry],
            test_files={"test_t001_slug.py": "x"},
            meta=_meta(),
        )


def test_rejects_duplicate_task_id(session_dir, workspace):
    entry = _good_entry(1)
    fname, content = _good_test(1)
    with pytest.raises(SeedWriteError, match="duplicate task id"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[entry, entry],
            test_files={fname: content},
            meta=_meta(),
        )


def test_rejects_empty_acceptance_criteria(session_dir, workspace):
    entry = {**_good_entry(1), "acceptance_criteria": []}
    fname, content = _good_test(1)
    with pytest.raises(SeedWriteError, match="acceptance_criteria"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[entry],
            test_files={fname: content},
            meta=_meta(),
        )


def test_rejects_test_file_outside_naming_pattern(session_dir, workspace):
    with pytest.raises(SeedWriteError, match=r"test_t<NNN>_<slug>\.py"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[_good_entry(1)],
            test_files={"test_one.py": "x"},
            meta=_meta(),
        )


def test_rejects_test_file_for_unknown_task(session_dir, workspace):
    fname, content = _good_test(2)  # T-002, but only T-001 in prd
    with pytest.raises(SeedWriteError, match="doesn't correspond to any task"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[_good_entry(1)],
            test_files={fname: content},
            meta=_meta(),
        )


def test_rejects_task_with_no_matching_test(session_dir, workspace):
    fname, content = _good_test(1)
    with pytest.raises(SeedWriteError, match="no test file for task"):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[_good_entry(1), _good_entry(2)],
            test_files={fname: content},  # missing T-002
            meta=_meta(),
        )


def test_validation_failure_writes_nothing(session_dir, workspace):
    """A rejected bundle must leave no partial state on disk."""
    fname, content = _good_test(2)
    with pytest.raises(SeedWriteError):
        FileSeedSink().write_seed(
            session_dir=session_dir,
            workspace=workspace,
            prd_entries=[_good_entry(1)],
            test_files={fname: content},
            meta=_meta(),
        )
    assert not (session_dir / "prd.json").exists()
    assert not (session_dir / "seed-meta.json").exists()
    assert not (workspace / "tests" / fname).exists()
