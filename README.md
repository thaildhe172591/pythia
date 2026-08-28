<p align="center">
  <img src="https://raw.githubusercontent.com/thaildhe172591/pythia/main/assets/logo.png" alt="pythia" width="280" />
</p>

# pythia

> Oracle's MCP gives your agent a connection. **pythia gives it the judgment to use it.**

**English** · [Tiếng Việt](README.vi.md) · **[The Complete Guide →](GUIDE.md)**

[![ci](https://github.com/thaildhe172591/pythia/actions/workflows/ci.yml/badge.svg)](https://github.com/thaildhe172591/pythia/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.9%2B-blue)

An Agent Skills + CLI kit for developing PL/SQL on Oracle Database with AI
coding agents (Claude Code, Codex, Cursor — any of the 76 agents `npx skills`
supports).

pythia is a **harness**: a book of working rules an agent studies and follows
when it sits next to a developer. Not a chatbot, not an autopilot — a
disciplined assistant with three properties a good one has: **knowledge** (it
asks the live schema, never its memory of one), **judgment** (it measures
before it proposes), and **obedience to rules it can recite** (gates it will
quote back to you rather than quietly skip).

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

## The operating model: Learn → Ask → Do

Give the agent a problem — "add a column and every procedure that maintains
it", "why does this report double rows", "port this fix" — and the kit walks
it through the same three movements a careful senior developer makes.

### 1 · Learn — understand before proposing

The agent studies four things, in order, with tools instead of guesses:

| It learns | How | Instead of |
|---|---|---|
| the problem's real shape | `deps`, `impact`, `plscope` — the exact dependency graph and usage sites | skimming code and hoping |
| the schema's ground truth | `src`, `cols`, `args`, `ddl`, `errors` against the live database | trusting a dump that drifted |
| the house style | `.pythia/conventions.md` + `conventions --scan/--check` — rules measured against real names | inventing a style per session |
| how this codebase already solves it | `similar` — the neighbours to imitate | writing the first thing that compiles |

Nothing in this phase writes. Reading is free, so the bar is: **no proposal
before the blast radius is known** (`pythia-impact`'s iron law) and **no line
written before the neighbours have been read** (`pythia-write`'s).

### 2 · Ask — the questions are part of the method, not an interruption

The kit makes the agent stop at exactly the moments where a human's judgment
is the missing input, and forbids it to guess past them:

- **Before any write**: the full preview — diff, dependents, warnings — is
  relayed verbatim, and the agent waits for a real yes. A compliment is not a
  yes. Silence is not a yes. And the wait is no longer a matter of obedience:
  the write cannot complete until the developer mints the approval themselves.
- **When the blast radius is large**: ten or more dependents, or anything
  cross-schema, goes to the developer *before code is written*, not after.
- **When sources of truth disagree**: a standards document says one thing,
  the schema does another — that gap is a question ("rule nobody follows,
  new-code-only, or drift?"), never a silent pick.
- **When something breaks**: exit 3 means *written but broken*. The agent
  reports it exactly so, with the ready rollback — reporting success here is
  the one sin the whole kit is built to prevent.
- **When policy refuses**: the refusal is relayed as information, not routed
  around.

### 3 · Do — act inside a pipeline that cannot lie

Only after Learn and Ask does anything touch the database, and then only
through one door: **snapshot → impact → preview → token → approve → apply →
verify → report**. DDL self-commits in Oracle, so the snapshot is the only real
undo — it runs first and no flag disables it. A content-bound token guarantees
what lands is byte-for-byte what was previewed, and a **developer-minted grant**
guarantees a person approved it at all: `pythia approve` runs only at a human's
own console, and without its one-time grant `apply --confirm` refuses. The
agent's command line is unchanged; what changed is that it now stops until
someone acts. A headless agent cannot `--yes` its own writes, cannot approve
them, and cannot loosen policy — each of those takes a human at a real terminal.

The same discipline holds when the *developer* does the work: `src` and
`impact` silently snapshot what they read, so even a change made by hand in
SQL Developer has a rollback file waiting (`history` lists them, drift is
reported when source moved with no apply behind it).

**Học – Hỏi – Làm** — Learn, Ask, Do. If the agent cannot show which phase it
is in, it is doing none of them.

## Install

```bash
npx pythia-plsql           # everything: pip install + skills picker + config scaffold
```

Or the same thing piecewise:

```bash
pip install pythia-plsql   # the CLI (thin driver — no Oracle Instant Client needed)
python -m pythia install   # skills into your agent + .pythia/ scaffold
pythia check               # fill in connections.json first, then verify
```

The pip package is the whole kit: with Node.js present, `pythia install`
runs `npx skills add` (77 agents, symlinked updates; `--source <git-url>`
for internal mirrors) — without Node it copies the bundled pack itself.
Prefer **global skills**: `pythia install -g` once per machine serves every
project, and per-project installs then skip the skills step automatically —
one copy anywhere means no skill ever shows up twice in the agent's menu. Skills alone: `npx skills add
thaildhe172591/pythia`, or `/plugin marketplace add thaildhe172591/pythia`.

`pip install` is **once per machine**; `pythia install` is **once per
project** — run it in each repo's root to drop the skills and a fresh
`.pythia/connections.json` there. The CLI always reads the config of the
project you are standing in (searched upward from the current directory,
no global fallback), so one global CLI never mixes up databases.

**Updating** follows the same split:

```bash
pip install --upgrade pythia-plsql   # new CLI version (once per machine)
pythia install                       # refresh a project's skills; config never touched
```

(`npx skills update` also works for skills installed via npx.)

Running from a clone works too — `python scripts/pythia.py <command>`; every
printed follow-up command matches how you invoked it. Windows, macOS, Linux
and WSL are all CI-tested.

## Commands

| Read | Understand | Write |
|---|---|---|
| `check` connectivity + counts | `deps` what it depends on | `apply` the six-step write |
| `ls` find objects | `impact` what depends on it | `journal` list · diff · export · restore |
| `src` source, compiler line numbers | `errors` compile errors, line:col | `policy` show · set |
| `args` signatures | `invalid` everything broken | `unistr` exact non-ASCII literals |
| `ddl` via DBMS_METADATA | `plscope` exact identifier usages | `agent-user` least-privilege setup |
| `cols` columns + types | `similar` programs named like this | `history` every captured version |
| `grep` search all source | | `approve` the developer's one-time grant |
| `sql` free query (SELECT/WITH only) | | |

Every command takes `--json` (machine output), `--conn` (pick a connection), and
caps output with explicit truncation markers so context windows stay intact.

**The safety net covers hand edits too**: `src` and `impact` snapshot the
object silently into the journal, each with a runnable rollback file, so a
change made later in SQL Developer still has something to go back to —
`pythia history <OBJECT>` lists the versions. Source that moved with no
apply behind it is reported as drift.

**Your house style is config, not folklore**: put naming patterns in
`.pythia/conventions.json` and apply previews warn when a new object's name
drifts; put the prose rules in `.pythia/conventions.md` and the skills make
every agent read them first (`pythia conventions` shows both).

## Security & write policy

**The account is the real security layer** — the policy file is an application-side
fence. Give the agent its own revocable credential with proxy authentication
(`agent_user[schema_owner]`, no `ANY` privileges, no owner password shared):

```bash
pythia agent-user --save   # SQL for the DBA + matching credential saved as <conn>_agent
pythia check               # after the DBA ran it: proxy session, warning gone
```

One run does both — the password is regenerated each run, so the SQL and the
saved config must come from the same run. Optional convenience: doing it by
hand with
[`examples/agent-user-setup.example.sql`](examples/agent-user-setup.example.sql)
works just as well.
`pythia check` warns when the session runs with more power than the task needs.

Using Claude Code? [`examples/claude-code-settings.example.json`](examples/claude-code-settings.example.json)
stops it prompting for the read-only commands and asks it to pause on
writes — optional, and yours to install
([why pythia does not](GUIDE.md#11-optional-claude-code-permission-settings)).

Per-group write policy, `.pythia/policy.json` (defaults shown):

| Group | Default | Is rollback real? |
|---|---|---|
| `plsql_source` | `confirm` | **Yes — completely.** Source is recoverable from `ALL_SOURCE`. |
| `data_dml` | `deny` | **No.** After commit only Flashback Query remains, within undo retention. Revalidation checks the row set *before* the write; it is not an undo. |
| `structural` | `deny` | **Almost never.** `DROP COLUMN` is permanent; a dropped table may be in the Recycle Bin. |
| `grants` | `deny` | Yes, but by hand. |
| `session` | `allow` | Not needed. |

The groups that cannot be snapshotted default to `deny` — and the refusal says that,
instead of "policy forbids it". Anonymous PL/SQL blocks are refused outright.
Unrecognized statements are refused, never guessed into a group.

Threat model and what a snapshot does **not** restore: [SECURITY.md](SECURITY.md).

Reads may flow through Oracle's official SQLcl MCP server (`sql -mcp`, keep `-R 4`);
it audits every interaction in `DBTOOLS$MCP_LOG`. **Writes never do** — only
`pythia apply` has the snapshot, preview, verify and journal.

## Skills

Eight skills teach the agent the workflow — gates, not suggestions:

`pythia-spec` (open decisions are asked, not assumed) · `pythia-setup` ·
`pythia-explore` · `pythia-impact` (before any change) ·
`pythia-write` (copy the codebase's conventions) · `pythia-apply` (the gate:
the developer approves the preview in chat) · `pythia-review` (antipatterns) ·
`pythia-conventions` (adopt a house style, verified against real names) ·
`pythia-skill-author` (capture your team's workflow as a skill).

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
