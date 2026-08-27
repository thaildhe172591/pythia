---
name: pythia-setup
description: Use when setting pythia up for a project or a machine - writing connections.json, creating a least-privilege agent user, wiring the SQLcl MCP server, or diagnosing a connection that will not open. Also the place to start when check prints a privilege warning.
---

# Setting Up

**Announce at start:** "Using pythia-setup to configure the database access."

**Phase:** Do — one-time harness setup; everything after runs through Learn → Ask → Do

Set up in this order: connection first (everything else needs it), the
least-privilege user second (the only protection that cannot be bypassed),
SQLcl MCP last (optional, reads only).

## 1. Connection

Create `.pythia/connections.json` at the project root — `pythia install`
scaffolds it (pip installs: `pip install pythia-plsql`), or copy
`examples/connections.example.json` from a clone. Fill it in. Rules pythia
applies:

- One entry: used as-is. Several entries: the path segment directly under the
  project root picks one (`root/DEV/...` → `DEV`), else the entry named by a
  top-level `"default": "<name>"`. Ambiguity is an error, never a guess.
- `--conn NAME` and `PYTHIA_CONNECTION` override everything; the
  `PYTHIA_USER/PYTHIA_PASSWORD/PYTHIA_DSN` variables bypass the file.
- The file holds credentials: it is gitignored — keep it that way, and keep
  it out of chat and screenshots.

Verify with `pythia check`: it prints who you are connected as and object
counts. A failure names the connection and what to check.

## 2. The agent's database user — the real protection

The policy file is an application-side fence; **Oracle grants are the only
layer an agent cannot walk around.** Oracle has no clean per-object form of
"may edit PL/SQL in that schema" — compiling into another schema needs
`CREATE ANY PROCEDURE`, which spans every schema on the instance.

The full layout is three accounts — admin (administration only), owner
(the schema, never DBA: a proxy session inherits everything the owner
holds), agent (logon only) — see GUIDE section 3 and
`examples/agent-user-setup.example.sql`. The core of it is **proxy
authentication**: a logon-only user that
connects *through* the schema owner. The agent never learns the owner's
password, revocation is one statement, the audit trail shows who really
connected, and the blast radius is the one development schema.

Run `pythia agent-user --save` — ONE run, in the project directory. It
prints the three-statement proxy SQL with a generated password and saves
the matching credential as connection `<conn>_agent` (the new default; the
owner entry stays untouched). The password is regenerated on every run, so
never preview first and save later — the SQL and the saved config must
come from the same run. Relay the SQL to the developer verbatim for a DBA
to execute, then verify with `pythia check`. `--json` gives the same
result machine-readable. Manual alternative:
`examples/agent-user-setup.example.sql`, names adapted.

Oracle's own guidance for LLM access, follow it: grant minimum privileges,
never point an LLM at a production database, audit its activity regularly.

`pythia check` warns on one line when the session holds `%ANY%` privileges or
runs as the schema owner directly. The goal state is: no warning.

## 3. SQLcl MCP server (optional, reads only)

If SQLcl 25.2+ is installed, agents can read through Oracle's official MCP
server: command `sql -mcp`. Example client config:

```json
{"mcpServers": {"sqlcl": {"command": "sql", "args": ["-mcp"]}}}
```

- Keep the default restrict level (`-R 4`, most restrictive): it blocks host
  commands and `@` scripts. Note it does NOT block DML/DDL inside `run-sql` —
  which is why **writes never go through MCP**: only `pythia apply` has the
  snapshot, preview, verify and journal.
- Built-in audit, worth telling the DBA about: every interaction lands in
  `DBTOOLS$MCP_LOG`; `V$SESSION.MODULE` shows the MCP client and
  `V$SESSION.ACTION` the LLM's name; generated SQL carries an
  `/* LLM in use */` comment.

## 4. What lives in `.pythia/`, and what creates it

Only `connections.json` is written by `install`. The rest appear when you ask
for them, so an empty `.pythia/` is a working one — nothing here is missing
until you want it.

| File | Created by | What it does |
|---|---|---|
| `connections.json` | `pythia install` | Where to connect. **Holds passwords — never open it**; `pythia connections` lists names, users, schemas and targets with no secrets. Gitignored. |
| `journal/` | first write or snapshot | Every captured version, with a runnable rollback per entry. |
| `conventions.json` | `pythia conventions --init` | Naming patterns per object type. Every apply preview warns when a new name drifts. |
| `conventions.md` | `pythia conventions --init` | The house rules in prose. **`pythia-write` reads this before writing anything**, and it outranks the generic patterns this pack ships with. |
| `policy.json` | `pythia policy set <group> <value>` | Pins the write policy. Absent means the built-in defaults, which are already the safe ones. |
| `settings.json` | you, by hand | Optional switches, e.g. `{"auto_snapshot": false}`. |

**Capturing a team's house style is the highest-value optional step.** Run
`pythia conventions --init`, then replace the placeholder patterns with the
real ones — `pythia similar <A_TYPICAL_NAME>` shows what the schema already
does, which beats inventing a scheme. Fill in `conventions.md` with the rules
that carry a cost when broken, and say what the cost is; a rule with a named
consequence gets followed. Commit both files to the project repo so the whole
team and every agent session works from the same rules.

## Done when

- `pythia check` connects, shows the right schema, and prints **no**
  privilege warning.
- `pythia policy` prints the write policy and the rollback table.
- The credentials file is untracked (`git status` does not show it).
