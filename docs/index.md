# Tilth

> *Prepare the ground, let the agent grow the work.*

A minimal long-running agent harness against an **OpenAI-compatible** LLM endpoint. Tested today against [OpenRouter](https://openrouter.ai); the OpenAI SDK underneath means other OpenAI-flavour gateways should work, but support for them is on the roadmap rather than validated. Built to learn (and demonstrate) the Brain/Hands/Session split, the Ralph loop, and the four memory channels described in Addy Osmani's [long-running agents](https://addyosmani.com/blog/long-running-agents/), [agent harness engineering](https://addyosmani.com/blog/agent-harness-engineering/), and [self-improving agents](https://addyosmani.com/blog/self-improving-agents/) posts.

![Brain / Hands / Session split — three boxes connected by flow arrows, with the files that implement each piece](assets/brain-hands-session.png)

*Brain / Hands / Session*
{: .caption }

**Audience:** This is an active research project for my work in [Altered Craft](https://alteredcraft.com). I do activly use it for real work, so I would advise it for single-dev / few-dev teams who want to *understand* what a long-running agent harness actually does. That is today (May-2026), in the future, we shall see.

**Target run:** I test with 10-60 minutes of autonomous work against an open model (default `deepseek/deepseek-v4-flash` on OpenRouter for the worker; the evaluator and prep interview default to `deepseek/deepseek-v4-pro`). Completing a task list against a small project on a per-session git worktree.

![The Ralph loop — PRD task to worker agent to validators to evaluator to commit, looping back, all inside a per-session git worktree](assets/ralph-loop.png)

*Tilth's Ralph loop*
{: .caption }

## What's in these docs

- **[Getting started](getting-started/installation.md)** — install, seed a task list with `prep-feature`, run the demo, resume / reset / visualize a session.
- **[Architecture](architecture/overview.md)** — the Brain / Hands / Session split, the memory channels.
- **[Deep dives](deep-dives/index.md)** — the two loops, the worker↔evaluator dialogue, token recording and enforcement, what the agent sees (and doesn't), the caps story, resume / reset mechanics. Honest, code-level walk-throughs for extending, debugging, or reasoning about the safety story.
- **[Reference](reference/safety-guards.md)** — safety guards.
