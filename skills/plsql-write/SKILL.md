---
name: plsql-write
description: Use when writing or modifying PL/SQL source - a procedure, function, package, trigger, or view - after impact is known. The codebase's conventions already exist; copy them instead of inventing style, and anchor every type to the database's reality.
---

# Writing PL/SQL

**Announce at start:** "Using plsql-write — mining the codebase's conventions first."

A codebase with thousands of procedures has already decided how procedures
look. Your job is to write one that a maintainer cannot tell from the
existing ones — not to introduce a better style.

## The Workflow

1. **Find the models.** `pythia similar <NEW_NAME>` ranks existing programs
   sharing name tokens; the `MATCHED_TOKENS` column says why. Open the top
   two or three with `pythia src` and imitate: naming, parameter prefixes,
   cursor style, error handling, comment style.
2. **Anchor the signatures.** For every program you call:
   `pythia args NAME` — real parameter names, order, types, defaults. Never
   guess a signature from memory of similar code.
3. **Anchor the types.** For every table you touch: `pythia cols TABLE` —
   then declare variables with `%TYPE` / `%ROWTYPE` against those columns
   instead of copying the current type by hand. The declaration then
   survives column changes.
4. **Write the file.** Rules the write path enforces — follow them here:
   - **One object per file.** Package spec and body are two files.
   - End the file with the PL/SQL block's `;` and a final line holding `/`.
   - Name the object unqualified, or qualified with the exact schema the
     connection targets — a mismatch is refused at apply time.
5. **Check yourself before handing off.** Reread against
   `reference/patterns.md` — cursor and bulk patterns, exception discipline,
   bind variables, commit ownership.

## Conventions outrank preferences

If the codebase writes explicit cursors where you would write `FOR r IN`,
write explicit cursors. If its parameter prefixes look dated, use them
anyway. A mixed-style codebase is worse than a consistently dated one —
propose style changes to the developer separately, never silently.

## When NOT to use this skill

- Understanding existing code → `plsql-explore`.
- Measuring what a change breaks → `plsql-impact` (must already be done).
- Landing the file on the database → `plsql-apply`, always — never
  `run-sql`, never `sqlplus`.
