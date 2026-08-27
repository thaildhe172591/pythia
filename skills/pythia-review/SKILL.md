---
name: pythia-review
description: Use when reviewing PL/SQL - a changed procedure or package, a proposed file, or an object suspected of causing trouble. Combines the database's own correctness signals with the antipattern checklist, and reports findings anchored to line numbers.
---

# Reviewing PL/SQL

**Announce at start:** "Using pythia-review — checking the database's signals first."

**Phase:** Learn → Ask — the database's own signals first, findings back to the developer

Review in two passes: what the database *knows* is wrong, then what the
checklist says is *likely* wrong. Machine signals first — they are free and
exact.

## Pass 1 — the database's own verdict

1. `pythia errors NAME` — compile errors and warnings with line:column.
   An object that does not compile needs no style review yet.
2. `pythia src NAME` — read the source with the compiler's line numbers, so
   findings can be anchored (`line 47: ...`).
3. `pythia impact NAME --depth 2` — how exposed is this object; a finding in
   something with 40 dependents outranks the same finding in a leaf.
4. For a proposed file (not yet applied): the compile verdict arrives at
   apply time — say explicitly that compilation is still unverified.

## Pass 2 — the antipattern checklist

Work through `reference/antipatterns.md` — and the project's own
`.pythia/conventions.md` when it exists, which outranks the generic list.
The seven entries, each with
wrong → right → why: row-by-row cursor loops, string-concatenated dynamic
SQL, `WHEN OTHERS THEN NULL`, `COMMIT` inside loops, hand-copied types
instead of `%TYPE`/`%ROWTYPE`, large OUT parameters without `NOCOPY`, and
convention drift against the codebase (`pythia similar` shows the house
style).

## Reporting findings

- Anchor every finding: `line N: <what> — <why it bites> — <the fix>`.
- Severity order: breaks correctness → silently loses data or errors →
  performance at scale → style drift.
- Verify the claim before writing it: read the actual lines with `src`;
  never report from memory of the diff alone.
- A clean review says what was checked, not just "looks good": compiles
  clean, no new INVALID, checklist passed.

## When NOT to use this skill

- Reviewing whether a change is *safe to apply* — that is `pythia-impact`
  plus `pythia-apply`'s preview; this skill judges the code itself.
- Reviewing non-PL/SQL application code — outside this skill's scope.
