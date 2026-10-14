# A robust Oracle development environment

What a team wants in place before an agent writes its first object. Run
`pythia doctor` (harness) and `pythia doctor --db` (database) to check it.

## 1. Separate environments, and mark the one that matters

| Environment | Who writes | In connections.json |
|---|---|---|
| Local / personal DEV | the developer and their agent | normal entry |
| Shared DEV / TEST | through `pythia apply` / `deploy` only | normal entry; agree autopilot use with the team |
| UAT / PROD | release process, DBA | `"protected": true` or `"env": "prod"` — autopilot refuses it, every write is approved one at a time — and better: no entry at all |

Oracle's own guidance for LLM access: never point it at production.

## 2. A local database of your own

Oracle Database Free (23ai) is free for development and runs in a
container — the fastest way to give each developer a schema nobody else
breaks:

```bash
docker run -d --name oracle-free -p 1521:1521 \
  -e ORACLE_PWD=<choose-one> \
  container-registry.oracle.com/database/free:latest
# service name: FREEPDB1 — connections.json "dsn": "localhost:1521/FREEPDB1"
```

Community images (e.g. `gvenzl/oracle-free`) start faster and can create an
app user on first boot. Either way: the schema owner is not DBA, and the
agent connects through its own proxy user (`pythia agent-user --save`).

## 3. Character set and length semantics

- `NLS_CHARACTERSET` should be **AL32UTF8**. Anything else cannot store every
  Vietnamese character in `VARCHAR2`; `pythia doctor --db` warns.
- Declare text columns `VARCHAR2(n CHAR)` explicitly. Changing
  `NLS_LENGTH_SEMANTICS` instance-wide has side effects on Oracle's own
  install and upgrade scripts — be explicit in the DDL instead.
- String literals in PL/SQL: `pythia unistr "<text>"` so the source survives
  any client that later edits it. `COMMENT ON ... IS` takes plain literals.
- Never rely on `NLS_DATE_FORMAT`: `TO_DATE('2026-10-14', 'YYYY-MM-DD')`.

## 4. Compiler settings for development

| Setting | Development value | Why |
|---|---|---|
| `PLSCOPE_SETTINGS` | `'IDENTIFIERS:ALL, STATEMENTS:ALL'` | exact usage answers (`pythia plscope`); `pythia apply` sets it per write |
| `PLSQL_WARNINGS` | `'ENABLE:ALL'` (dev), e.g. `'ENABLE:SEVERE','ENABLE:PERFORMANCE'` | the compiler finds unreachable code, implicit conversions; read with `pythia errors` |
| `PLSQL_OPTIMIZE_LEVEL` | 2 (default) | 3 inlines aggressively; leave it unless measured |
| `PLSQL_CCFLAGS` | e.g. `'debug:TRUE'` in dev only | conditional compilation for debug code that never ships |

Ask the DBA to set warnings and PL/Scope at the schema or system level for
DEV, so every compile — not only pythia's — records them.

## 5. Shared-schema hygiene

- `ALTER SESSION SET DDL_LOCK_TIMEOUT = 30` in tooling sessions, so DDL waits
  for a busy object instead of failing with ORA-00054.
- Recycle Bin on (`RECYCLEBIN = ON`, the default) — the only cheap way back
  from a dropped table. Never `PURGE` in scripts.
- Undo retention long enough for Flashback Query over a working day on DEV —
  the only route back from committed DML.
- Editions (EBR): if the schema is editions-enabled, objects are EDITIONABLE
  or NONEDITIONABLE and Oracle refuses to change that through CREATE OR
  REPLACE (ORA-38824). pythia reads the property from the database.

## 6. Tests and quality gates

- **utPLSQL v3** in a shared `UT3` schema (or per developer): test packages
  with `--%suite` / `--%test` annotations, run by CI or the developer
  (`ut.run`). `pythia doctor --db` says whether it is installed.
- `pythia lint` in CI over the release scripts: exit 2 on any error.
- `.pythia/conventions.json` (naming + lint rules) and `conventions.md`
  committed, so every session and every reviewer works from the same rules.

## 7. The agent's client

| Client | Recommended mode | Approval door |
|---|---|---|
| Claude Code, reviewing each change | `default` or `acceptEdits` | `pythia_approve` (MCP, prompts in every mode but dontAsk); AskUserQuestion card as fallback |
| Claude Code, autonomous session | `acceptEdits` + an autopilot lease | one `pythia_approve` for the lease; writes then land within it |
| Claude Code, `bypassPermissions` | works — the gates are pythia's, not the client's | same doors; never on a shared or protected connection |
| Codex | default sandbox, interactive approvals | `pythia_approve` via MCP elicitation (≥ v0.120); never `danger-full-access` |

`dontAsk` mode denies both AskUserQuestion and the MCP approver: approvals
then only happen at the developer's console (`pythia approve <token>`), or
through an autopilot lease started there (`pythia autopilot on`).

After `pythia install`: restart the session, run `pythia doctor`, and fix
every `fail` line — each one is a setting that will stop approvals from
arriving. A `fail` on **hook launch** means a hook's interpreter cannot import
pythia, so the guard is off: common when pythia lives in a venv or pipx and
Claude Code starts from a desktop app or IDE. Re-run `pythia install` — it pins
that machine's interpreter in `.claude/settings.local.json` and Claude Code's
local MCP scope, never in the shared files.
