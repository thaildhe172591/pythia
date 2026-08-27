---
name: pythia-apply
description: Use when a PL/SQL change is ready to reach the database - applying a CREATE OR REPLACE, restoring from the journal, or any other write. Runs the six-step flow through pythia apply and enforces the gate - the developer sees the preview and approves in chat before anything is written, and a broken result is never reported as success.
---

# Applying PL/SQL Changes

**Announce at start:** "Using pythia-apply — I'll preview the change first."

**Phase:** Ask → Do — the preview is relayed and approved before the one write door opens

DDL in Oracle commits itself. There is no transaction to roll back — the
snapshot pythia takes before writing is the only undo that exists. This skill
exists so that safety net is always used, and used honestly.

## The Iron Law

```
NO WRITE THE DEVELOPER HAS NOT SEEN AND APPROVED
```

Applies to every write: new objects, fixes, restores, batch runs. No
exceptions for "trivial" changes — a one-line change can invalidate twelve
dependents.

Three moves are FORBIDDEN for agents, and the CLI enforces the first two
(no terminal attached → refusal):

- `--yes` — it is the developer's flag, never yours.
- `pythia policy set <group>` to anything LOOSER — loosening policy is the
  developer's decision; hand them the exact command to run themselves.
- Passing `--confirm` in the same turn as the preview. The preview ends
  your turn; the token is only used after the developer's approval
  arrives as a NEW message.

## When the change will NOT go through apply

Sometimes the developer runs the file themselves — a DBA executes it, a
release process owns it, or policy denies the group. The preview still ran,
so a rollback file for the version currently live already exists at
`.pythia/journal/<entry>/restore.sql`. Never let a change leave your hands
for manual execution without naming that path in the same message as the
.sql file. `pythia history <OBJECT>` lists every captured version.

## The Workflow

**Before step 1:** if this conversation has not yet seen a standalone impact
analysis for this object, run `pythia-impact` first. The `impact:` line inside
apply's preview is confirmation of a number you already knew — if it is the
first time anyone sees it, a step was skipped.

1. **Preview.** Run `pythia apply <file>`. This writes nothing: it snapshots,
   computes impact, prints a diff, a warning block, and a confirm token.
2. **Relay the preview to the developer — verbatim.** Show the diff, the
   `impact:` line, and any `!` warning exactly as printed. Do not summarize
   the diff away; the developer approves what they see, not your paraphrase.
3. **Wait for an explicit yes.** A yes is an instruction to proceed with THIS
   preview: "yes, apply it", "go ahead", "looks good — do it". Not a yes: a
   compliment without a go-ahead, a question, silence, or an approval that
   was given for an earlier preview. If the developer changes the file
   instead, start over at step 1.
4. **Apply** by running the exact `To apply:` command pythia printed (it
   contains the token). If pythia says the token is stale, the file or the
   database changed since the preview — go back to step 1, never "retry".
   If the fresh preview's before-side no longer matches what you last saw,
   say so explicitly: someone else may have changed the object on this
   shared database, and the developer must know that before approving.
5. **Read the exit code — it is the verdict. Never through a pipe:**
   `apply … | tail` returns *tail's* code, always 0. Unpiped, or `${PIPESTATUS[0]}`.

   | Exit | Meaning | What you must do |
   |------|---------|-----------------|
   | 0 | applied, compiled clean, nothing newly INVALID | report done, mention the restore id |
   | 1 | refused (policy, classification, stale token) | relay the printed reason and its fix; do not work around it |
   | 3 | **written but broken** — compile errors or other objects now INVALID | see below |
6. **On exit 3, never report success.** Say plainly that the change went in
   and broke something, show the compile errors (line:col) and the list of
   newly INVALID objects, and offer the `To undo:` command pythia printed.
   Fixing forward is allowed only after the developer sees this state.

## Restores

`pythia journal restore <id>` is itself a write and goes through the same six
steps and the same gate: preview the reverse diff to the developer, wait for
yes, then confirm. Note: restoring an object that did not exist before means
DROP — policy will refuse it under `structural: deny`, and that refusal is
correct; relay it instead of forcing a way around.

## Batch mode

`--yes` skips the pause, not the preview — output and journal are identical.
Use it only when the developer explicitly asked for unattended application
("apply all of these"). A frustrated "stop asking" grants `--yes` for the
task at hand, not from now on: confirm the scope once ("this batch, or
standing?") and default to this-batch-only. A standing `--yes` never extends
to restores. Rules for a batch:

- **Stop at the first exit 3.** Never keep applying onto a broken state.
- Afterwards report: one line per success, full detail (errors, newly
  INVALID, restore command) for the failure, and the exact list of files
  that were NOT applied because the batch stopped.

## Never bypass the write path

When `pythia apply` is available, do not write through anything else — not
SQLcl MCP `run-sql`, not `sqlplus`, not a driver script. Those paths have no
snapshot, no impact preview, no verify, no journal. If apply refuses a
statement, that refusal is information for the developer, not an obstacle to
route around.

## Is rollback real? Be honest about it

| Group | Is rollback real? |
|---|---|
| `plsql_source` | **Yes — completely.** The source is recoverable from `ALL_SOURCE`. |
| `data_dml` | **No.** After commit only Flashback Query remains, and only within undo retention. |
| `structural` | **Almost never.** `DROP COLUMN` is permanent; a dropped table may be in the Recycle Bin. |
| `grants` | Yes, but by hand. |
| `session` | Not needed. |

Never promise "we can always roll back" — that sentence is only true for the
first row, and saying it generally misleads the developer at the exact moment
the stakes are highest.

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

## When NOT to use this skill

- Reading or exploring — use `pythia-explore`.
- Judging blast radius before editing — use `pythia-impact` (always run it
  before proposing a change; apply's preview is confirmation, not discovery).
- Editing files the developer has not asked to land on the database yet.

> Invocation note: examples say `pythia ...`; run it however this project
> provides it (for example `python scripts/pythia.py ...`). Every pythia
> output prints follow-up commands in the correct form — prefer pasting those.
