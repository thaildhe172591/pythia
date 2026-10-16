---
name: pythia-apply
description: Use when a PL/SQL change is ready to reach the database - applying a CREATE OR REPLACE, restoring from the journal, or any other write. Runs the six-step flow through pythia apply and enforces the gate - the developer sees the preview and approves in chat before anything is written, and a broken result is never reported as success.
---

# Applying PL/SQL Changes

**Announce at start:** "Using pythia-apply — I'll preview the change first."

**Phase:** Ask → Do — the preview is relayed and approved before the one write door opens

DDL in Oracle commits itself, so the snapshot pythia takes before writing is
the only undo there is — for DML not even that. This skill is how it gets used.

## The Iron Law

```
NO WRITE THE DEVELOPER HAS NOT SEEN AND APPROVED
```

Every write: new objects, fixes, restores, batch runs. No exception for
"trivial" — a one-line change can invalidate twelve dependents.

Four moves are FORBIDDEN for agents, and pythia enforces all four (the CLI
refuses without a terminal; the pre-tool hook denies the last):

- `--yes` — it is the developer's flag, never yours.
- Minting the approval. `pythia approve <token>` is the developer's console
  command; an agent cannot run it. Your door is to ASK (step 3): the grant is
  minted only from the developer's own answer.
- `pythia policy set <group>` to anything LOOSER, and `pythia autopilot on` —
  the developer's decisions; hand them the exact command to run themselves.
- Writing around the gate — sqlplus, SQLcl `run-sql`, a driver script.

One move is yours: `--confirm` — only after `Approve`, or a console approve.
**Exception: an active autopilot lease** (`pythia-autopilot`) the developer
approved — then `pythia apply <file>` lands covered statements itself, and
the preview says `autopilot: applying under lease`, or why it does not.

## When the change will NOT go through apply

A DBA or a release process runs it, or policy denies the group: the preview
still left a rollback for the live version at
`.pythia/journal/<entry>/restore.sql` (`pythia history <OBJECT>`). Name it.

## The Workflow

**Before step 1:** if this conversation has not seen a standalone impact
analysis for this object, run `pythia-impact` first — apply's `impact:` line
confirms a number you already knew.

1. **Preview.** `pythia apply <file>` writes nothing: it snapshots, computes
   impact, prints a diff, warnings, lint findings, and a confirm token.
2. **Relay the preview — verbatim.** The diff, the `impact:` line, every `!`
   warning, exactly as printed. The developer approves what they see, not
   your paraphrase.
3. **Ask for approval — the first door that works, the next one unasked:**
   - **Claude Code: `AskUserQuestion`** — its own dialog, shown in every
     client. `pythia approve --card <token>` prints the card; the question
     text is that card and nothing else — no word before it, no translation
     after it. The hook compares question to card and mints nothing when
     they differ. Explanation goes in the option descriptions. Header
     `pythia`; options exactly `Approve` / `Reject`; up to four tokens per
     call; never fill in `answers`. When the hook says the answer did not
     count for any reason but a Reject, go straight to the next door.
   - **Then the `pythia_approve` tool** (MCP; Codex starts here) with the
     token: pythia's own card. If it says the client did not show it, move on.
   - **Last:** relay `pythia approve <token>` for the developer's terminal.
   Anything but `Approve` is not approval: stop and ask what should change.
4. **Apply** by running the exact `then the agent:` line pythia printed. Two
   refusals are normal here, and neither is a malfunction:
   - *"no developer approval is on file"* — the answer was not `Approve`, or
     the hook is not installed. Say so and wait; retrying does not create it.
   - *"the confirmation token does not match"* — the file or the database
     changed since the preview. Go back to step 1, never "retry"; the fresh
     preview warns by itself when the object moved outside pythia.
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
approval included. Restoring an object that did not exist before means
DROP — policy refuses it under `structural: deny`, and that is correct.

## More than one statement

A script, a release, a table with its comments: `pythia-deploy` — one
preview of every statement, one approval, run in order, stopped at the first
exit 3. "Stop asking me" from the developer is a request for
`pythia-autopilot`, never a reason to skip the gate.

## Never bypass the write path

Not SQLcl MCP `run-sql`, not `sqlplus`, not a driver script: no snapshot, no
preview, no verify, no journal. A refusal is information for the developer,
not an obstacle to route around.

## Is rollback real? Be honest about it

| Group | Is rollback real? |
|---|---|
| `plsql_source` | **Yes — completely.** The source is recoverable from `ALL_SOURCE`. |
| `comments` | **Yes.** The previous comment is snapshotted before every write. |
| `data_dml` | **No.** After commit only Flashback Query remains, and only within undo retention. Revalidation checks the row set *before* the write; it is not an undo. |
| `structural` | **Almost never.** `DROP COLUMN` is permanent; a dropped table may be in the Recycle Bin. |
| `grants` | Yes, but by hand. |
| `session` | Not needed. |

Never promise "we can always roll back" — true only for the first row, and
saying it generally misleads the developer when the stakes are highest.

`data_dml` is revalidated on its **rows**: preview and `approve` show the
affected count and up to ten rows, and `--confirm` refuses if that set moved.
`MERGE`, and anything unmeasurable, is refused rather than guessed at.

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
| "I'll run approve myself to unblock this" | The developer's act. Console approve refuses you; the hook mints only from *their* answer. Routing around it is what this gate exists to stop. |
| "I'll add a line of my own around the card" | Any added word voids it. Explanation goes in the option description — or use the `pythia_approve` tool. |
| "Lint flagged it, but it compiles" | Errors are fixed before the preview reaches the developer; autopilot will not land them. |

## When NOT to use this skill

- Reading → `pythia-explore`. Blast radius → `pythia-impact` (the preview
  confirms, it does not discover). Several statements → `pythia-deploy`.

> Examples say `pythia ...`; every pythia output prints follow-ups in the
> form this project runs — prefer pasting those.
