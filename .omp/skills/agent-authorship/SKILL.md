---
name: agent-authorship
description: Tag PR titles and review threads with [auth:<model>:<harness>] authorship so same-model self-review can be refused.
---

# Agent authorship

Every PR you open and every review thread you start MUST begin with an authorship tag:

`[auth:<model>:<harness>]`

Examples: `[auth:muse-spark-1.3-contributor:omp]`, `[auth:sonnet-5.5:claude-code]`, `[auth:gpt-4o:codex]`.

## Tag format

- First token of the PR title, e.g. `[auth:gpt-4o:codex] Add token refresh`.
- First line of each review-thread body (inline comments included).
- `<model>`: lowercase model slug, no spaces. If you genuinely do not know it, use `unknown-model` — never omit the tag.
- `<harness>`: `omp`, `claude-code`, `codex`, or your harness slug, lowercase.
- Shape: `\[auth:([a-z0-9][a-z0-9._-]*):([a-z0-9][a-z0-9-]*)\]`.
- Never alter or drop an existing tag when retitling; never claim another model/harness identity.

## Review gate

Before submitting any review or approval:

1. Read the authorship tag on the PR title (fallback: first thread).
2. No tag → treat as human-authored → you MAY review; still tag your own threads.
3. Same `<model>` (case-insensitive), any harness → MUST refuse:
   `[auth:<your-model>:<your-harness>] refusing review: authored by same model (<model>). Needs a different model.`
4. Same harness + different model → allowed.

Rationale: same-model review is self-review; same harness with a different model is independent enough.
