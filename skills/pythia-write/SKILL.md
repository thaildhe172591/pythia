---
name: pythia-write
description: Use when writing or modifying PL/SQL source - a procedure, function, package, trigger, or view - after impact is known and the spec is settled (open decisions go through pythia-spec first). The codebase's conventions already exist; copy them instead of inventing style, and anchor every type to the database's reality.
---

# Writing PL/SQL

**Announce at start:** "Using pythia-write — mining the codebase's conventions first."

**Phase:** Learn → Do — mine the neighbours first, then draft; landing it is pythia-apply's job

**Before anything:** if the project has `.pythia/conventions.md`, read it —
house rules there outrank every generic pattern below, and
`pythia conventions` shows the naming patterns the apply preview will check. If the project has none and the developer
describes a house style, offer `pythia conventions --init` — captured once,
it applies to every future session instead of being re-explained.

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
   - **Non-ASCII string literals** (Vietnamese messages, any accented text):
     never paste raw — run `pythia unistr "<text>"` and use the printed
     `unistr('...')`, so the text survives every client/DB charset exactly.
5. **Check yourself before handing off.** Reread against
   `reference/patterns.md` — cursor and bulk patterns, exception discipline,
   bind variables, commit ownership.

## Scope is the developer's sentence, not yours

Change ONLY the objects the developer named. When impact shows dependents
that will break, you REPORT them with the list and a proposal — fixing
them is a separate request that needs its own explicit approval. "While I
was there" is how an asked-for table edit becomes seven unasked procedure
rewrites.

## Conventions outrank preferences

If the codebase writes explicit cursors where you would write `FOR r IN`,
write explicit cursors. If its parameter prefixes look dated, use them
anyway. A mixed-style codebase is worse than a consistently dated one —
propose style changes to the developer separately, never silently.

## When NOT to use this skill

- Understanding existing code → `pythia-explore`.
- Measuring what a change breaks → `pythia-impact` (must already be done).
- Landing the file on the database → `pythia-apply`, always — never
  `run-sql`, never `sqlplus`.
