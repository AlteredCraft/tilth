"""session_label is best-effort, used in picker menus to make bare session ids
legible. The contract:

- Tries seed-meta.json `tldr` first (the seeder's TL;DR — most human-readable).
- Falls back to prd.json first entry's `id: title` (still informative).
- Returns "" if neither yields a string; never raises.
- Truncates to max_chars with an ellipsis so picker rows stay one-line.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth.session import session_label


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "20260526-091445-abc"
    d.mkdir()
    return d


def _write_meta(session_dir: Path, payload: dict) -> None:
    (session_dir / "seed-meta.json").write_text(json.dumps(payload))


def _write_prd(session_dir: Path, payload: list) -> None:
    (session_dir / "prd.json").write_text(json.dumps(payload))


def test_label_from_tldr_first_bullet(session_dir: Path) -> None:
    _write_meta(
        session_dir,
        {
            "tldr": (
                "- **T-001:** Scaffold CSVExporter — set up exporters/csv.py\n"
                "- **T-002:** Wire format flag — add --format=csv to CLI\n"
            )
        },
    )
    assert session_label(session_dir).startswith("**T-001:**")


def test_label_strips_leading_bullet_chars(session_dir: Path) -> None:
    _write_meta(session_dir, {"tldr": "  *  T-001: do the thing\n"})
    assert session_label(session_dir) == "T-001: do the thing"


def test_label_falls_back_to_prd_when_tldr_missing(session_dir: Path) -> None:
    _write_prd(
        session_dir,
        [
            {
                "id": "T-001",
                "title": "Scaffold the package",
                "description": "",
                "acceptance_criteria": [],
            },
            {
                "id": "T-002",
                "title": "Wire it up",
                "description": "",
                "acceptance_criteria": [],
            },
        ],
    )
    assert session_label(session_dir) == "T-001: Scaffold the package"


def test_label_falls_back_to_prd_when_tldr_is_blank(session_dir: Path) -> None:
    _write_meta(session_dir, {"tldr": "   \n  \n"})
    _write_prd(session_dir, [{"id": "T-001", "title": "From prd"}])
    assert session_label(session_dir) == "T-001: From prd"


def test_label_empty_when_neither_file_exists(session_dir: Path) -> None:
    assert session_label(session_dir) == ""


def test_label_handles_malformed_seed_meta(session_dir: Path) -> None:
    (session_dir / "seed-meta.json").write_text("{not valid json")
    _write_prd(session_dir, [{"id": "T-001", "title": "Fallback works"}])
    assert session_label(session_dir) == "T-001: Fallback works"


def test_label_handles_malformed_prd(session_dir: Path) -> None:
    (session_dir / "prd.json").write_text("{not valid json")
    # No tldr, malformed prd → "", not a crash.
    assert session_label(session_dir) == ""


def test_label_handles_unexpected_shapes(session_dir: Path) -> None:
    _write_meta(session_dir, {"tldr": 42})  # not a string
    _write_prd(session_dir, "not a list")  # type: ignore[arg-type]
    assert session_label(session_dir) == ""


def test_label_truncates_long_strings(session_dir: Path) -> None:
    long = "T-001: " + ("x" * 200)
    _write_meta(session_dir, {"tldr": f"- {long}"})
    result = session_label(session_dir, max_chars=60)
    assert len(result) == 60
    assert result.endswith("…")


def test_label_respects_explicit_max_chars(session_dir: Path) -> None:
    _write_meta(session_dir, {"tldr": "- short"})
    assert session_label(session_dir, max_chars=80) == "short"
