# Data dictionary reference

Which Oracle views back each pythia command, and what to know when going
beyond them with `pythia sql`.

## Views behind the commands

| Question | View(s) | pythia command |
|---|---|---|
| What objects exist, status, last change | `ALL_OBJECTS` | `ls`, `invalid`, `similar` |
| PL/SQL source, line-exact | `ALL_SOURCE` | `src`, and the apply snapshot |
| Compile errors with line:column | `ALL_ERRORS` | `errors` |
| Procedure/function signatures | `ALL_ARGUMENTS` | `args` |
| Columns, types, nullability, defaults | `ALL_TAB_COLUMNS` | `cols` |
| Object-level dependency graph | `ALL_DEPENDENCIES` | `deps`, `impact` |
| Identifier declarations and usages | `ALL_IDENTIFIERS` (PL/Scope) | `plscope` |
| SQL statements inside PL/SQL, per table | `ALL_STATEMENTS` (PL/Scope) | `plscope` on a table |
| DDL reconstruction | `DBMS_METADATA.GET_DDL` | `ddl` |
| Session's dangerous privileges | `SESSION_PRIVS` | `check` warning |

`ALL_*` views show what the connected user has rights on. Empty results can
mean "does not exist" or "not visible to this user" — with a least-privilege
setup, prefer the proxy connection so visibility matches the schema owner's.

## PL/Scope

PL/Scope data exists only for objects compiled while it was enabled:

```sql
ALTER SESSION SET plscope_settings = 'IDENTIFIERS:ALL, STATEMENTS:ALL';
ALTER PROCEDURE <name> COMPILE;
```

- Statement capture (`ALL_STATEMENTS`) needs Oracle 12.2+.
- Recompiling objects on a **shared** schema affects everyone using it —
  agree it with the team first. pythia never runs these for you.
- Check what an object was compiled with: `ALL_PLSQL_OBJECT_SETTINGS`
  (`PLSCOPE_SETTINGS` column).

## DBMS_METADATA notes

`ddl` disables `STORAGE` and `SEGMENT_ATTRIBUTES` transforms deliberately:
segment clauses are noise for code review and burn context. If you need
tablespace/storage detail, ask with `pythia sql` against `DBA_/ALL_SEGMENTS`
(subject to privileges).

## Licensing boundary — stay on the free side by default

Safe on every edition, no extra license:

- All `ALL_*` / `USER_*` dictionary views used above
- `V$SESSION`, `EXPLAIN PLAN`, Statspack

**Extra-cost** — do not query these unless the site confirms the license:

- `DBA_HIST_*` (AWR) — requires Diagnostics Pack
- SQL Performance Analyzer / SQL Tuning Advisor — Tuning/RAT packs

pythia's built-in queries are license-safe. When writing free-form `sql`,
keep to the free list unless told otherwise — a senior DBA reads restraint
here as a sign the tooling understands Oracle.
