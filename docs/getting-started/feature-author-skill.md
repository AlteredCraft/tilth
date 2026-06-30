# Authoring a feature with the skill

Tilth's quality is dominated by the quality of the feature directory you hand it. The task files *are* the contract — the worker builds to them, the evaluator gates the diff against them, and there's no codified test gate underneath ([The task format](../deep-dives/task-format.md)). So authoring is the high-leverage moment, and the docs repeat that you can "draft the files with whatever agent you like, the templates are model-fillable." `tilth-feature-author` is the *purpose-built* version of that — a [Claude Code](https://claude.com/claude-code) skill that interviews you, anchored on your repo's real code, and writes the directory for you.

It's optional. The harness runs identically whether you authored the markdown by hand or with the skill; the output is the same `overview.md` + `T-NNN-<slug>.md` format. The skill just makes the contract sharp before any tokens are spent on a run.

> **It authors, it does not run.** The skill's whole surface is the feature directory. It never invokes the harness, edits your `AGENTS.md`, or writes harness state — `tilth run` stays yours to invoke.

## Install it

The skill ships in the Tilth repo under [`skills/tilth-feature-author/`](https://github.com/AlteredCraft/tilth/tree/main/skills/tilth-feature-author). Install it user-level so it's available in whatever repo you point it at (the skill operates on your *target* project, not on the Tilth clone):

```bash
# from a Tilth clone — copy:
cp -R skills/tilth-feature-author ~/.claude/skills/

# …or symlink, to track the clone as it updates:
ln -s "$(pwd)/skills/tilth-feature-author" ~/.claude/skills/tilth-feature-author
```

Claude Code discovers any skill under `~/.claude/skills/`. Confirm it loaded with `/tilth-feature-author` in the prompt; if it doesn't autocomplete, restart Claude Code so it re-scans.

## Use it

Invoke it with a one-line feature description and the path to the target repo:

```
/tilth-feature-author Add CSV export to the reports CLI — repo at ~/projects/reports
```

The target must be a **git repo with at least one commit** (Tilth's worktree machinery requires it; a brand-new project just needs `git init` + an initial commit — the skill handles greenfield/MVP projects, adding the scaffold, test-harness, and README tasks you didn't name). From there the skill runs a fixed shape:

1. **Scans your code** — steered by the feature, not exhaustive. It reads your build/test toolchain, the modules the feature touches, your `AGENTS.md`/`CLAUDE.md` conventions, and any existing `.tilth/<feature>/` directories (so it appends rather than overwrites).
2. **Interviews you** — one question at a time, anchored on what it found. Decision-style questions (which module, which encoding) come as multiple-choice; the open ones (motivation, scope, risks) are free-form. It pushes back when a slice reads too big and proposes a concrete de-scope.
3. **Slices the work** — into 3–8 small, ordered, atomically-committable tasks, each pinned to externally-checkable acceptance criteria. You iterate on the proposed slicing together until you both agree — this is the heart of it.
4. **Writes the directory** — `overview.md` (goal, context, and the high-leverage scope boundaries) plus one `T-NNN-<slug>.md` per task, then prints a chat summary (one line per task, open questions, any blockers it surfaced). Nothing is written to disk but the feature directory.

Then it stops and suggests next steps — a dry-run read-through, picking an evaluator model, and the `tilth run` command — for *you* to run.

## What it deliberately won't do

The skill holds an **altitude ceiling**: task descriptions say *what* and *where*, never *how*. The deepest it goes is naming the file, module, or type the work lands in — never a new function signature, a control-flow construct, or a literal string the worker should emit. That's by design: the evaluator gates on the diff-vs-criteria, never on the description's steps, so transcribing an implementation adds zero gate strength while stripping the worker's agency. Every detail that must hold becomes an observable acceptance criterion instead. (This is the same reasoning behind [Agent visibility](../architecture/agent-visibility.md) — the worker chooses the approach.)

## When to skip it

- **A bug fix that fits one task** — write the single `T-NNN` file directly; a full interview is overkill.
- **Adding one more task to a feature that already exists in the same style** — edit the directory directly.
- **You're not using Claude Code** — the skill is bound to that runtime. The format is plain markdown, so author it by hand against [the task format](../deep-dives/task-format.md), or drive any other model with the same templates.

## See also

- [Using on your own project](your-own-project.md) — the end-to-end flow the skill feeds into: prep, author, run, review.
- [The task format](../deep-dives/task-format.md) — the format reference the skill targets (parsing rules, who reads each field).
- [Agent visibility](../architecture/agent-visibility.md) — why the *what/where-not-how* altitude ceiling matters.
