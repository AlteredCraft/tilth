"""Atomic on-disk implementation of SeedSink.

The seed bundle is four pieces of state that must commit together:
  - `sessions/<id>/prd.json`           — the task list
  - `sessions/<id>/seed-meta.json`     — interview audit trail
  - `<workspace>/tests/test_*.py`      — one per task
  - `session_prepared` event           — appended by the caller, not here

"Atomic" here means staged-then-renamed: each file is written under a
`.tmp` sibling and `os.replace`'d into place. A crash mid-write leaves the
prior state untouched. We do NOT cross-mount: tmp files are siblings of
their destinations so the rename is on the same filesystem.

Contract validation happens here, not in the model. The interview prompt
asks for shape; this module enforces it. A bad seed (collision, missing
test, wrong filename pattern) raises `SeedWriteError` and writes nothing.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

REQUIRED_PRD_KEYS = ("id", "title", "description", "acceptance_criteria")
TEST_FILE_RE = re.compile(r"^test_t\d{3,}_[a-z0-9_]+\.py$")
TASK_ID_RE = re.compile(r"^T-\d{3,}$")


class SeedWriteError(RuntimeError):
    pass


class FileSeedSink:
    def write_seed(
        self,
        session_dir: Path,
        workspace: Path,
        prd_entries: list[dict[str, Any]],
        test_files: dict[str, str],
        meta: dict[str, Any],
    ) -> None:
        _validate(prd_entries, test_files)

        prd_path = session_dir / "prd.json"
        meta_path = session_dir / "seed-meta.json"
        tests_dir = workspace / "tests"

        normalised = [_normalise_entry(e) for e in prd_entries]

        # Stage everything first; only swap into place once every write succeeded.
        # Track (tmp_path, final_path) pairs so we can clean up on failure.
        staged: list[tuple[Path, Path]] = []
        try:
            staged.append(_stage_text(prd_path, json.dumps(normalised, indent=2) + "\n"))
            staged.append(_stage_text(meta_path, json.dumps(meta, indent=2) + "\n"))
            tests_dir.mkdir(parents=True, exist_ok=True)
            for filename, content in test_files.items():
                staged.append(_stage_text(tests_dir / filename, content))
        except Exception:
            for tmp, _ in staged:
                _unlink_quiet(tmp)
            raise

        for tmp, final in staged:
            os.replace(tmp, final)


def _validate(
    prd_entries: list[dict[str, Any]],
    test_files: dict[str, str],
) -> None:
    if not prd_entries:
        raise SeedWriteError("prd_entries is empty; a seed must include at least one task")
    if not test_files:
        raise SeedWriteError("test_files is empty; every task needs a matching test file")

    seen_ids: set[str] = set()
    for entry in prd_entries:
        missing = [k for k in REQUIRED_PRD_KEYS if k not in entry]
        if missing:
            raise SeedWriteError(
                f"prd entry missing required key(s) {missing}: {entry!r}"
            )
        tid = entry["id"]
        if not isinstance(tid, str) or not TASK_ID_RE.match(tid):
            raise SeedWriteError(
                f"task id must match T-NNN with 3+ digits, got {tid!r}"
            )
        if tid in seen_ids:
            raise SeedWriteError(f"duplicate task id in prd_entries: {tid}")
        seen_ids.add(tid)
        if not isinstance(entry["acceptance_criteria"], list) or not entry["acceptance_criteria"]:
            raise SeedWriteError(
                f"task {tid}: acceptance_criteria must be a non-empty list"
            )

    for filename in test_files:
        if not TEST_FILE_RE.match(filename):
            raise SeedWriteError(
                f"test filename {filename!r} doesn't match test_t<NNN>_<slug>.py — "
                "the harness's pytest filter skips files outside this pattern"
            )

    # Cross-check: every task has a matching test file, and vice versa.
    expected_prefixes = {f"test_{tid.lower().replace('-', '')}_" for tid in seen_ids}
    for filename in test_files:
        if not any(filename.startswith(p) for p in expected_prefixes):
            raise SeedWriteError(
                f"test file {filename!r} doesn't correspond to any task in prd_entries"
            )
    covered = {
        p for p in expected_prefixes if any(fn.startswith(p) for fn in test_files)
    }
    missing_tests = expected_prefixes - covered
    if missing_tests:
        raise SeedWriteError(
            f"no test file for task(s) {sorted(missing_tests)}: every prd entry "
            "needs a matching test_<id>_<slug>.py"
        )


def _normalise_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Always-`pending` shape — drop anything the model snuck in."""
    return {
        "id": entry["id"],
        "title": entry["title"],
        "description": entry["description"],
        "acceptance_criteria": list(entry["acceptance_criteria"]),
        "status": "pending",
    }


def _stage_text(final: Path, content: str) -> tuple[Path, Path]:
    final.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{final.name}.", suffix=".tmp", dir=str(final.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
    except Exception:
        _unlink_quiet(tmp)
        raise
    return tmp, final


def _unlink_quiet(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass
