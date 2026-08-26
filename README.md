# pythia

> Oracle's MCP gives your agent a connection. **pythia gives it the judgment to use it.**

[![ci](https://github.com/thaildhe172591/pythia/actions/workflows/ci.yml/badge.svg)](https://github.com/thaildhe172591/pythia/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.9%2B-blue)

An Agent Skills + CLI kit for developing PL/SQL on Oracle Database with AI coding
agents (Claude Code, Codex, Cursor — any of the 76 agents `npx skills` supports).
Explore schemas too big to dump, measure blast radius **before** touching anything,
and land changes through a snapshot-verified write path that never lies about rollback.

## Why ask the database instead of reading dumps

A real mid-size system, repo export vs live database, audited in 2026:

| Object type | In the dump | In the database | Verdict |
|---|---|---|---|
| Procedures | 3,827 | 3,827 | matched |
| Tables | 952 | 952 | matched |
| **Types** | **0** | **115** | **all missing** |
| **Packages** | **0** | **9** | **all missing** |
| **Indexes** | **116** | **1,016** | **~89% missing** |

Code that "reads fine" against the dump references types and packages the dump never
heard of. Every pythia command asks the live data dictionary instead — and every
truncated output says so, so an agent never mistakes a partial answer for a full one.

## How it works

```
developer chats with the agent
        │
skills/  teach the agent when to ask, when to stop, when to ask YOU
        │
pythia   CLI — expert queries, impact analysis, the six-step write path
        │
Oracle   data dictionary: ALL_SOURCE, ALL_DEPENDENCIES, ALL_ERRORS, PL/Scope
```

The write path is the heart: **snapshot → impact → preview → apply → verify → report**.
DDL self-commits in Oracle — the snapshot is the only real undo, so it always runs
first and no flag can turn it off. A 6-hex token binds the write to exactly what was
previewed; exit codes make honesty machine-readable
(`0` clean · `1` refused · `3` **written but broken — never reported as success**).

## Install

```bash
# 1. skills into your agent (Claude Code, Codex, Cursor, ...)
npx skills add thaildhe172591/pythia

# or as a Claude Code plugin
/plugin marketplace add thaildhe172591/pythia

# 2. the CLI (thin driver — no Oracle Instant Client needed)
git clone https://github.com/thaildhe172591/pythia && cd pythia
pip install oracledb

# 3. connection + verify
cp examples/connections.example.json .pythia/connections.json   # then fill it in
python scripts/pythia.py check
```

Tip: `alias pythia="python3 scripts/pythia.py"` (macOS/Linux) makes every example
below paste-able as written. Windows, macOS, Linux and WSL are all CI-tested.

## Commands

| Read | Understand | Write |
|---|---|---|
| `check` connectivity + counts | `deps` what it depends on | `apply` the six-step write |
| `ls` find objects | `impact` what depends on it | `journal` list · diff · export · restore |
| `src` source, compiler line numbers | `errors` compile errors, line:col | `policy` show · set |
| `args` signatures | `invalid` everything broken | |
| `ddl` via DBMS_METADATA | `plscope` exact identifier usages | |
| `cols` columns + types | `similar` programs named like this | |
| `grep` search all source | | |
| `sql` free query (SELECT/WITH only) | | |

Every command takes `--json` (machine output), `--conn` (pick a connection), and
caps output with explicit truncation markers so context windows stay intact.

**Your house style is config, not folklore**: put naming patterns in
`.pythia/conventions.json` and apply previews warn when a new object's name
drifts; put the prose rules in `.pythia/conventions.md` and the skills make
every agent read them first (`pythia conventions` shows both).

## Security & write policy

**The account is the real security layer** — the policy file is an application-side
fence. Give the agent its own revocable credential with proxy authentication
(`agent_user[schema_owner]`, no `ANY` privileges, no owner password shared):
see [`examples/agent-user-setup.example.sql`](examples/agent-user-setup.example.sql).
`pythia check` warns when the session runs with more power than the task needs.

Per-group write policy, `.pythia/policy.json` (defaults shown):

| Group | Default | Is rollback real? |
|---|---|---|
| `plsql_source` | `confirm` | **Yes — completely.** Source is recoverable from `ALL_SOURCE`. |
| `data_dml` | `deny` | **No.** After commit only Flashback Query remains, within undo retention. |
| `structural` | `deny` | **Almost never.** `DROP COLUMN` is permanent; a dropped table may be in the Recycle Bin. |
| `grants` | `deny` | Yes, but by hand. |
| `session` | `allow` | Not needed. |

The groups that cannot be snapshotted default to `deny` — and the refusal says that,
instead of "policy forbids it". Anonymous PL/SQL blocks are refused outright.
Unrecognized statements are refused, never guessed into a group.

Reads may flow through Oracle's official SQLcl MCP server (`sql -mcp`, keep `-R 4`);
it audits every interaction in `DBTOOLS$MCP_LOG`. **Writes never do** — only
`pythia apply` has the snapshot, preview, verify and journal.

## Skills

Seven skills teach the agent the workflow — superpowers-style gates, not suggestions:

`plsql-setup` · `plsql-explore` · `plsql-impact` (impact **before** any change) ·
`plsql-write` (copy the codebase's conventions) · `plsql-apply` (the gate: the
developer sees the preview and approves in chat before anything is written) ·
`plsql-review` (seven antipatterns) · `plsql-skill-author` (capture *your team's*
workflow as a new skill, mined from the live schema).

## Compatibility

| | |
|---|---|
| OS | Windows, macOS, Linux, WSL — full test matrix in CI |
| Python | 3.9+ · stdlib + `python-oracledb` (thin mode) only |
| Oracle | core works broadly; PL/Scope statement capture needs 12.2+; license-safe views only |
| Agents | any `npx skills` agent (76) · native Claude Code plugin |

## Star History

<a href="https://www.star-history.com/?repos=thaildhe172591%2Fpythia&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=thaildhe172591/pythia&type=date&theme=dark&legend=top-left&sealed_token=OnPCuXPZZEbpQk5_Eor5ZB0fTeMzMN1nmrsDJ8qqahouiJt4-IoDvjONdD05i2D2PhfDC5kwd6CUQeBsWGNV20gt2-4HSD-RygX3h0Ni0lrbQnRh60EN3A" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=thaildhe172591/pythia&type=date&legend=top-left&sealed_token=OnPCuXPZZEbpQk5_Eor5ZB0fTeMzMN1nmrsDJ8qqahouiJt4-IoDvjONdD05i2D2PhfDC5kwd6CUQeBsWGNV20gt2-4HSD-RygX3h0Ni0lrbQnRh60EN3A" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=thaildhe172591/pythia&type=date&legend=top-left&sealed_token=OnPCuXPZZEbpQk5_Eor5ZB0fTeMzMN1nmrsDJ8qqahouiJt4-IoDvjONdD05i2D2PhfDC5kwd6CUQeBsWGNV20gt2-4HSD-RygX3h0Ni0lrbQnRh60EN3A" />
 </picture>
</a>

## Contributing

Tests need **no database** — the fakes prove the safety properties (snapshot before
write, deny touches nothing, stale tokens refused). See [CONTRIBUTING.md](CONTRIBUTING.md).

MIT — see [LICENSE](LICENSE).
