---
name: pythia-apply
description: Use when a PL/SQL change is ready to reach the database - applying a CREATE OR REPLACE, restoring from the journal, or any other write. Runs the six-step flow through pythia apply and enforces the gate - the developer sees the preview and approves in chat before anything is written, and a broken result is never reported as success.
---

# Applying PL/SQL Changes

**Announce at start:** "Using pythia-apply — I'll preview the change first."

**Phase:** Ask → Do — the preview is relayed and approved before the one write door opens

DDL in Oracle commits itself. There is no transaction to roll back — the
snapshot pythia takes before writing is the only undo there is, and this skill
exists so that net is always used, and used honestly.

## The Iron Law

```
NO WRITE THE DEVELOPER HAS NOT SEEN AND APPROVED
```

Every write: new objects, fixes, restores, batch runs. No exception for
"trivial" — a one-line change can invalidate twelve dependents.

Three moves are FORBIDDEN for agents, and the CLI enforces all three (no
terminal attached → refusal):

- `--yes` — it is the developer's flag, never yours.
- `pythia approve <token>` — the developer's command, run at their own
  terminal. It mints the one-time grant `--confirm` requires, and an agent
  cannot run it. Relay the line; never run it yourself.
- `pythia policy set <group>` to anything LOOSER — loosening policy is the
  developer's decision; hand them the exact command to run themselves.

One move is yours but never in the same turn as the preview: `--confirm`. The
preview ends your turn; the token is used only after approval arrives as a NEW
message.

## When the change will NOT go through apply

Sometimes the developer runs the file themselves — a DBA executes it, a
release process owns it, or policy denies the group. The preview still ran, so
a rollback file for the live version exists at
`.pythia/journal/<entry>/restore.sql`. Never let a change leave your hands for
manual execution without naming that path alongside the .sql file (`pythia
history <OBJECT>` lists every captured version).

## The Workflow

**Before step 1:** if this conversation has not seen a standalone impact
analysis for this object, run `pythia-impact` first — apply's `impact:` line
confirms a number you already knew.

1. **Preview.** `pythia apply <file>` writes nothing: it snapshots, computes
   impact, prints a diff, warnings, and a confirm token.
2. **Relay the preview — verbatim.** The diff, the `impact:` line, every `!`
   warning, exactly as printed. The developer approves what they see, not
   your paraphrase.
3. **Wait for an explicit yes, and for the approval to be minted.** The
   preview prints two follow-up lines — `pythia approve <token>` for the
   developer, `apply --confirm` for you. Relay both. A yes is an instruction
   to proceed with THIS preview: "yes, apply it", "go ahead". Not a yes: a
   compliment, a question, silence, or approval given for an earlier preview.
   If the developer changes the file instead, start over at step 1.
4. **Apply** by running the exact `then the agent:` line pythia printed. Two
   refusals are normal here, and neither is a malfunction:
   - *"no developer approval is on file"* — they have not run `approve` yet.
     Say so and wait; retrying does not create approval.
   - *"the confirmation token does not match"* — the file or the database
     changed since the preview. Go back to step 1, never "retry". If the fresh
     preview's before-side differs from what you last saw, say so: someone may
     have changed the object on this shared database.
5. **Read the exit code — it is the verdict. Never through a pipe:**
   `apply … | tail` returns *tail's* code, always 0. Unpiped, or `${PIPESTATUS[0]}`.

   | Exit | Meaning | What you must do |
   |------|---------|-----------------|
   | 0 | applied, compiled clean, nothing newly INVALID | report done, mention the restore id |
   | 1 | refused (policy, classification, stale token) | relay the printed reason and its fix; do not work around it |
   | 3 | **written but broken** — compile errors or other objects now INVALID | see below |
6. **On exit 3, never report success.** Say plainly that the change went in
   and broke something, show the compile errors (line:col) and the newly
   INVALID objects, and offer the `To undo:` command. Fixing forward is
   allowed only after the developer sees this state.

## Restores

`pythia journal restore <id>` is itself a write: same six steps, same gate,
approval included. Preview the reverse diff, relay both follow-up lines, wait
for their approve, then confirm. Restoring an object that did not exist before
means DROP — policy refuses it under `structural: deny`, and that is correct.

## Batch mode

`--yes` skips the pause, not the preview — and it is the developer's flag: at
their terminal it *is* the approval, so no separate approve is needed. A
frustrated "stop asking" grants it for the task at hand, not from now on:
confirm the scope once and default to this-batch-only. It never covers restores.

- **Stop at the first exit 3.** Never keep applying onto a broken state.
- Afterwards report: one line per success, full detail (errors, newly INVALID,
  restore command) for the failure, and the files NOT applied because the
  batch stopped.

## Never bypass the write path

When `pythia apply` is available, do not write through anything else — not
SQLcl MCP `run-sql`, not `sqlplus`, not a driver script. Those paths have no
snapshot, no impact preview, no verify, no journal. A refusal from apply is
information for the developer, not an obstacle to route around.

## Is rollback real? Be honest about it

| Group | Is rollback real? |
|---|---|
| `plsql_source` | **Yes — completely.** The source is recoverable from `ALL_SOURCE`. |
| `data_dml` | **No.** After commit only Flashback Query remains, and only within undo retention. |
| `structural` | **Almost never.** `DROP COLUMN` is permanent; a dropped table may be in the Recycle Bin. |
| `grants` | Yes, but by hand. |
| `session` | Not needed. |

Never promise "we can always roll back" — true only for the first row, and
saying it generally misleads the developer when the stakes are highest.

## Red Flags — STOP if you catch yourself thinking

| Thought | Reality |
|---------|---------|
| "It's a tiny change, skip the preview" | Tiny changes invalidate dependents too. Preview. |
| "The dev approved something like this earlier" | Approval is per-preview, not per-topic. Ask again. |
| "Exit 3, but my part compiled — report done" | Something is broken that was not. That is not done. |
| "Token is stale, I'll just take the new one" | The content changed. The developer must see the new preview. |
| "apply refused it; run-sql will take it" | The refusal is the product working. Relay it. |
| "I'll restore quietly to clean up my mistake" | Restores are writes. Same gate, same visibility. |
| "`$?` said 0 after I piped to tail" | That was tail's 0. Read pythia's own words, or its unpiped code. |
| "The dev said 'stop asking' once" | That covered that task, not forever. Re-confirm scope on the next one. |
| "I'll run approve myself to unblock this" | The developer's console act. It refuses you, and routing around it is what this gate exists to stop. |
| "No approval on file — I'll retry until it works" | Retrying does not create approval. Relay the line, wait for a human. |

## When NOT to use this skill

- Reading or exploring — use `pythia-explore`.
- Judging blast radius before editing — use `pythia-impact` (apply's preview
  is confirmation, not discovery).
- Editing files not yet meant to land on the database.

> Invocation note: examples say `pythia ...`; run it however this project
> provides it (e.g. `python scripts/pythia.py ...`). Every pythia output
> prints follow-up commands in the right form — prefer pasting those.
