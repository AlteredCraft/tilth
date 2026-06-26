# Releasing to PyPI

How a maintainer cuts a Tilth release. Publishing is automated: tag a GitHub
Release and a workflow builds the package and uploads it to
[PyPI](https://pypi.org/project/tilth/) over **Trusted Publishing** (OIDC) — no
API tokens are stored anywhere.

The workflow lives at [`.github/workflows/release.yml`](https://github.com/AlteredCraft/tilth/blob/main/.github/workflows/release.yml).
It runs on `release: published`, builds with `uv build`, smoke-tests the wheel's
`tilth` entry point, and publishes with `uv publish`.

## One-time setup

Done once per project, before the first release.

### 1. Register the trusted publisher on PyPI

Because `tilth` doesn't exist on PyPI yet, register it as a **pending
publisher** — PyPI creates the project on the first successful upload.

1. Sign in to [PyPI](https://pypi.org/) → **Account settings** → **Publishing**.
2. Under **Add a new pending publisher**, choose **GitHub** and fill in:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `tilth` |
   | Owner | `AlteredCraft` |
   | Repository name | `tilth` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

The **Environment name** must match the `environment:` in `release.yml`. If you
leave it blank here, also remove the `environment: pypi` line from the workflow —
the two must agree or the OIDC claim is rejected.

### 2. Create the GitHub environment

In the repo: **Settings** → **Environments** → **New environment** → name it
`pypi`. Optionally add required reviewers to gate publishes behind a manual
approval. This is the recommended secure pattern; the environment also scopes
the OIDC token to release runs only.

> **TestPyPI first (optional).** To rehearse end-to-end, register a matching
> pending publisher on [TestPyPI](https://test.pypi.org/) and add a
> `--publish-url https://test.pypi.org/legacy/` step (or a separate workflow).
> A throwaway version like `0.0.1rc1` lets you verify the whole path without
> burning a real version number — PyPI refuses to re-upload a version, ever.

## Cutting a release

1. **Bump the version.** Edit `version` in `pyproject.toml` (Tilth is
   single-sourced there). Follow [SemVer](https://semver.org/); the tag below
   must match — `version = "0.2.0"` → tag `v0.2.0`.
2. **Land it on `main`** via the normal PR flow, and make sure CI is green
   (tests, ruff, `mkdocs build --strict`).
3. **Draft a GitHub Release.** **Releases** → **Draft a new release** → create a
   new tag `vX.Y.Z` targeting `main`, write release notes, **Publish release**.
4. The **Release to PyPI** workflow runs automatically. Watch it under
   **Actions**; on success the new version appears on
   [pypi.org/project/tilth](https://pypi.org/project/tilth/) within a minute.
5. **Verify** the published artifact installs and runs cleanly:

   ```bash
   uvx tilth@X.Y.Z --help
   ```

## Publishing manually (fallback)

If you ever need to publish from your machine instead of CI — build and upload
with an API token:

```bash
uv build                       # writes dist/tilth-X.Y.Z.{tar.gz,whl}
UV_PUBLISH_TOKEN=pypi-… uv publish
```

Create the token at PyPI → **Account settings** → **API tokens**, scoped to the
`tilth` project once it exists. Prefer the automated trusted-publishing path
above; a token is a long-lived secret you then have to guard and rotate.

## What ships in the package

The wheel bundles the package code plus the runtime assets the CLI needs:
`tilth/data/env.example` (used by `tilth init`), `tilth/prompts/*.md`, and the
`tilth/visualize/` web assets. Hatchling includes everything under `tilth/` by
default. Dev-only tools (`pytest`, `ruff`) live in the `dev`
[dependency group](https://peps.python.org/pep-0735/) and are **never** shipped
to end users — only `openai`, `pydantic`, `rich`, and `python-dotenv` are
declared as runtime dependencies. After changing dependencies, sanity-check the
build in isolation:

```bash
uv build
uv run --isolated --no-project --with dist/tilth-*.whl tilth --help
```

That installs the freshly built wheel with **only** its declared runtime
dependencies, so a missing or mis-scoped dependency fails loudly here rather
than in a user's environment.
