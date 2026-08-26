---
name: plsql-explore
description: Use when you need to understand anything in an Oracle schema - finding objects, reading PL/SQL source, signatures, table columns, DDL, searching code, or asking who uses what. The database is the only source of truth; repo dumps and exports drift and lie.
---

# Exploring the Schema

**Announce at start:** "Using plsql-explore — asking the database directly."

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
dump never heard of. Read files only when the database is unreachable, or to
compare a repo version against the live one.

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
