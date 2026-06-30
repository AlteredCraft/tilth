# Skills

Optional [Claude Code](https://claude.com/claude-code) skills that ship with Tilth.
They're *resources you install into Claude Code*, not part of the harness — Tilth
runs fine without them.

## `tilth-feature-author/`

Interviews you about a feature or refactor — anchored on the target repo's actual
code — and writes out a Tilth feature directory for you: an `overview.md` plus one
`T-NNN-<slug>.md` task file per slice, conventionally at `<repo>/.tilth/<feature>/`.
That directory is exactly what you hand to `tilth run`. Authoring is the
high-leverage moment (the task files *are* the contract the evaluator gates on),
and this skill is the purpose-built way to get that contract sharp.

**Install** (user-level, so it's available in any repo you point it at):

```bash
# copy
cp -R skills/tilth-feature-author ~/.claude/skills/

# …or symlink, to track this clone
ln -s "$(pwd)/skills/tilth-feature-author" ~/.claude/skills/tilth-feature-author
```

Then, from inside (or pointed at) your target repo, invoke `/tilth-feature-author`
with a one-line feature description and the repo path.

Full story — what it does, the interview workflow, and when *not* to reach for it —
is in the docs: [Authoring a feature with the skill](https://alteredcraft.github.io/tilth/getting-started/feature-author-skill/).
