---
name: plsql-setup
description: Use when setting pythia up for a project or a machine - writing connections.json, creating a least-privilege agent user, wiring the SQLcl MCP server, or diagnosing a connection that will not open. Also the place to start when check prints a privilege warning.
---

# Setting Up

**Announce at start:** "Using plsql-setup to configure the database access."

Set up in this order: connection first (everything else needs it), the
least-privilege user second (the only protection that cannot be bypassed),
SQLcl MCP last (optional, reads only).

## 1. Connection

Create `.pythia/connections.json` at the project root — copy
`examples/connections.example.json` and fill it in. Rules pythia applies:

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

The workable pattern is **proxy authentication**: a logon-only user that
connects *through* the schema owner. The agent never learns the owner's
password, revocation is one statement, the audit trail shows who really
connected, and the blast radius is the one development schema.

Run `examples/agent-user-setup.example.sql` (as a DBA, names adapted), then
set the connection's user to `"agent_user[schema_owner]"`.

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

## Done when

- `pythia check` connects, shows the right schema, and prints **no**
  privilege warning.
- `pythia policy` prints the write policy and the rollback table.
- The credentials file is untracked (`git status` does not show it).
