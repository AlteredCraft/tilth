"""The template `tilth init` writes ships inside the package (so it survives
`uv tool install`), with a human-facing copy at the repo root. Guard against the
two drifting apart.
"""

from __future__ import annotations

from pathlib import Path

import tilth


def test_packaged_env_template_matches_repo_root_example():
    pkg_dir = Path(tilth.__file__).resolve().parent
    packaged = pkg_dir / "data" / "env.example"
    assert packaged.is_file(), "packaged template missing — tilth init can't scaffold"

    repo_root = pkg_dir.parent / ".env.example"
    if repo_root.is_file():  # present in a checkout; absent in a pure wheel install
        assert packaged.read_text() == repo_root.read_text()
