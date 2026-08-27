---
name: pythia-explore
description: Use when you need to understand anything in an Oracle schema - finding objects, reading PL/SQL source, signatures, table columns, DDL, searching code, or asking who uses what. ALSO use when a .sql file, dump, export, migration script or repo copy of an object appears to answer the question already, including one the developer points you at - because those files drift from the database and are wrong in ways that read as correct. The database is the only source of truth.
---

# Exploring the Schema

**Announce at start:** "Using pythia-explore — asking the database directly."

**Phase:** Learn

## The principle: ask the database, never the dump

Repositories of exported `.sql` files go stale the day after export. A real
mid-size system, audited in 2026, compared its repo dump against the live
database:

| Object type | In the dump | In the database | Verdict |
|---|---|---|---|
| Procedures | 3,827 | 3,827 | matched |
| Tables | 952 | 952 | matched |
| **Types** | **0** | **115** | **all missing** |
| **Packages** | **0** | **9** | **all missing** |
| **Indexes** | **116** | **1,016** | **~89% missing** |

Code that "reads fine" against the dump can reference types and packages the
dump never heard of.

**When the developer hands you a `.sql` file** — "explain this procedure",
"here is the export, what does it do" — the file is a claim, not the truth.
Read it, then run `pythia src <NAME>` and answer from the database. If the
two differ, say so first: which is newer is exactly what the developer needs
to know, and it is invisible from the file alone. Never answer about a
database object from a file alone while the database is reachable.

Read files as the answer only when the database is unreachable — and say
that is what you are doing.

## What you need → what you run

| Need | Command |
|---|---|
| Is the connection alive, what schema | `pythia check` |
| Find objects by name | `pythia ls "PKG_%"` |
| Read source, with the compiler's line numbers | `pythia src NAME` (`--body`, `--spec`) |
| A procedure/function signature | `pythia args NAME` |
| Columns and real data types | `pythia cols TABLE_NAME` |
| Full DDL | `pythia ddl TABLE NAME` |
| Search all PL/SQL text | `pythia grep "text"` |
| What an object depends on | `pythia deps NAME` |
| What depends on an object | `pythia impact NAME` |
| Exact identifier usages (beats grep) | `pythia plscope NAME` |
| Programs named like this one | `pythia similar NAME` |
| Everything currently broken | `pythia invalid`, `pythia errors` |
| A free-form question | `pythia sql "select ..."` (SELECT/WITH only) |

## Rules that keep answers honest

- **Truncation is always announced.** Outputs end with `-- truncated ...` or
  set `"truncated": true` in `--json`. If you do not see the marker, you saw
  everything; if you do, say so or fetch more (`--limit`, `--offset`,
  `--max-lines`) — never present a truncated list as complete.
- **`plscope` before `grep`** for "where is X used": PL/Scope is recorded by
  the compiler and exact; grep matches comments and look-alike names. If
  PL/Scope has no data, the command says so and grep is the fallback.
- **`src` line numbers are the compiler's own** — an error at `line 47`
  means line 47 in `src` output. No offset arithmetic.
- Default output caps exist to protect your context window; raise them only
  for the object you are actually working on.

Deeper reference — which dictionary views back these commands, PL/Scope
enablement, licensing boundaries: `reference/data-dictionary.md`.
