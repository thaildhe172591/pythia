# pythia — The Complete Guide

**English** · [Tiếng Việt](GUIDE.vi.md) · Short version: [README.md](README.md)

From install to daily workflow, the security model and troubleshooting.
Every command pastes as written.

## The operating model — read this first

Learn → Ask → Do. The agent studies the schema and the house style before
proposing (`deps`, `impact`, `src`, `similar`, `conventions`); it stops and
asks at the moments where the developer's judgment is the missing input (the
preview gate, large blast radius, document-vs-schema conflicts, every
refusal, every exit 3); only then does it act, through the one write door
with snapshot, token and verify. `pythia guide` prints the full model from
the tool itself — on platforms with no skill support, that page is the
contract. Each skill declares which movement it serves in its `**Phase:**`
line.

## Contents

1. [Installing](#1-installing)
2. [Per-project connections](#2-per-project-connections)
3. [The least-privilege agent user](#3-the-least-privilege-agent-user)
4. [Reading and understanding a schema](#4-reading-and-understanding-a-schema)
5. [The write path: apply](#5-the-write-path-apply)
6. [The journal — snapshots and restore](#6-the-journal--snapshots-and-restore)
7. [Policy, settings, conventions](#7-policy-settings-conventions)
8. [Exact non-ASCII literals: unistr](#8-exact-non-ascii-literals-unistr)
9. [The agent skill pack](#9-the-agent-skill-pack)
10. [Troubleshooting](#10-troubleshooting)
11. [Optional: Claude Code permission settings](#11-optional-claude-code-permission-settings)

---

## 1. Installing

### One command, everything

```bash
npx pythia-plsql
```

Finds Python → `pip install pythia-plsql` (CLI + queries + bundled skills) →
`pythia install` (skills + config scaffold).

### Piecewise, same result

```bash
pip install pythia-plsql   # the CLI, thin driver — no Oracle Instant Client
python -m pythia install -g   # skills GLOBALLY: once per machine, every project
cd my-project && python -m pythia install   # per project: scaffolds .pythia/ only
python -m pythia check        # fill in connections.json, then verify
```

**`pythia: command not found`?** pip writes the executable into a scripts
directory that is usually not on PATH — pip warns about it, and it is why the
commands above say `python -m pythia`, which always works. To get the short
`pythia` form, let the tool put itself there:

```bash
python -m pythia install --add-to-path   # Windows: edits your user PATH only
```

Then open a **new** terminal — a running one keeps the environment it started
with, and so do its tabs.

> **If you previously ran a `SetEnvironmentVariable('PATH', "$env:PATH;...", 'User')`
> one-liner** (this guide once suggested one, and the shape is everywhere
> online): it copied your system PATH into your user PATH, because `$env:PATH`
> is the two merged. Check with
> `[Environment]::GetEnvironmentVariable('PATH','User') -split ';'` — if you
> see `C:\Windows\system32` and friends in there, they do not belong. Back the
> value up to a file first, then remove the entries that also appear in
> `[Environment]::GetEnvironmentVariable('PATH','Machine')`.

**Global-first**: the skill pack lives in `~/.claude/skills` — one copy. A
project `pythia install` that detects the global pack **skips the skills
step** — a second copy doubles every entry in the agent's menu. Want skills
committed with a repo instead? Remove the global pack, then run
`pythia install` in the project.

- With Node.js the skills step runs `npx skills add` (77 agents; at a TTY you
  get the interactive agent picker). `--source <git-url>` installs the pack
  from an internal mirror.
- Without Node the bundled pack is copied directly — the pip package is the
  whole kit.

### Updating

```bash
pip install --upgrade pythia-plsql   # new CLI (once per machine)
pythia install -g                    # refresh the global skill pack
```

Config is never touched by updates. Skills installed via npx:
`npx skills update`.

### Running from a clone (contributors)

```bash
git clone https://github.com/thaildhe172591/pythia && cd pythia
pip install oracledb
python scripts/pythia.py check
```

Printed follow-up commands always match how you invoked the tool.

## 2. Per-project connections

`.pythia/connections.json` — scaffolded by `pythia install`, **gitignored,
holds credentials, keep it out of chat and screenshots**:

```json
{
  "default": "dev",
  "dev":     { "host": "db-dev", "port": 1521, "service_name": "orclpdb",
               "user": "app_agent[app_owner]", "password": "...",
               "schema": "APP_OWNER" },
  "staging": { "host": "db-stg", "port": 1521, "service_name": "orclpdb",
               "user": "app_agent[app_owner]", "password": "...",
               "schema": "APP_OWNER" }
}
```

Connection resolution order (never a guess):

1. `--conn NAME` on the command
2. `PYTHIA_CONNECTION` (names an entry)
3. `PYTHIA_USER` / `PYTHIA_PASSWORD` / `PYTHIA_DSN` (+ `PYTHIA_SCHEMA`) —
   bypasses the file entirely; handy for testing one credential
4. The file, searched **upward from the current directory** (`PYTHIA_CONFIG`
   points at another file): one entry → used as-is; several → the path
   segment directly under the project root picks (`root/DEV/...` → entry
   `DEV`), then the top-level `"default"`; still ambiguous → an error
   listing the options

**No global fallback** — a globally installed CLI can never mix up
databases between projects; outside any project it errors plainly.

## 3. The least-privilege agent user

### The three-role layout — settled once, reused everywhere

**Advisory, not a requirement.** This layout is the author's recommendation —
one extra hardening layer at the role tier, offered as guidance. Your DBA
owns your security model; pythia works with whatever accounts you point it
at, and takes no responsibility for a site's privilege design. Use it, adapt
it, or ignore it.

One database, three accounts, three jobs that must not mix:

| Account | Privileges | Job | Owns objects? |
|---|---|---|---|
| `APP_ADMIN` | DBA (or your site's admin role) | administration only — create users, grants, Data Pump | never |
| `APP_OWNER` | the eight CREATE privileges + quota. **Never DBA, never RESOURCE** | owns the schema; the developer's daily account | yes — the single source |
| `APP_AGENT` | `CREATE SESSION`, nothing else | the AI agent's credential; proxies into the owner | never |

**Why three and not two.** A proxy session inherits the owner's *entire*
power. Leave DBA on the owner "for convenience" and every agent session is an
instance-wide DBA — which is why admin duties live in a separate account the
agent can never reach, and the owner keeps only what development needs.

**Why not an agent schema with `ANY` grants instead.** `CREATE ANY PROCEDURE`
spans *every schema on the instance*. On a shared instance, one wrong run
touches another team's — or another tenant's — code. `ANY` is never the
answer here; proxy scoping is: full power inside exactly one schema, zero
outside it.

```sql
-- Run as the site DBA, once per environment. Names are yours to change;
-- passwords are real passwords from day one, never the username.
CREATE USER app_admin IDENTIFIED BY "<its own strong password>";
GRANT DBA TO app_admin;                     -- admin work only; owns nothing

CREATE USER app_owner IDENTIFIED BY "<its own strong password>"
  QUOTA UNLIMITED ON users;
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE,
      CREATE PROCEDURE, CREATE TRIGGER, CREATE TYPE, CREATE SYNONYM
  TO app_owner;

CREATE USER app_agent IDENTIFIED BY "<its own strong password>";
GRANT CREATE SESSION TO app_agent;
ALTER USER app_owner GRANT CONNECT THROUGH app_agent;
-- Cut the agent off later, owner untouched:
--   ALTER USER app_owner REVOKE CONNECT THROUGH app_agent;
```

`connections.json` mirrors the split. The agent entry is the default; the
direct-owner entry exists for the developer and makes `check` warn when used
— that warning is the design working, not a problem:

```json
{
  "default": "agent_dev",
  "agent_dev": { "user": "app_agent[app_owner]", "schema": "APP_OWNER", "...": "..." },
  "dev":       { "user": "app_owner",            "schema": "APP_OWNER", "...": "..." }
}
```

Verify the whole triangle in two commands: `pythia connections` (who exists,
no secrets shown) and `pythia check` (connected as the owner via proxy, no
privilege warning). The rest of this section automates the agent leg and
cleans up an owner that grew too powerful.

**The database account is the real security layer** — the policy file is an
application-side fence. The pattern is **proxy authentication**: the agent
has its own credential and connects *through* the schema owner — it never
learns the owner's password, revocation is one statement, the audit trail
shows who really connected, and the blast radius is one dev schema.

```bash
pythia agent-user --save   # ONE run, exactly once
```

Prints the three-statement SQL and saves the matching credential to
`connections.json` (entry `<conn>_agent`, new default; the owner entry is
untouched — switch back with `--conn <old-name>`). The command **asks the
database before writing SQL**:

- The agent user already exists → the `ALTER USER ... IDENTIFIED BY ...
  ACCOUNT UNLOCK` form (avoids ORA-01920, clears ORA-28000 locks)
- The owner's grants are inspected → the output **states up front** whether
  `check` will come back clean or still warn
- `--json` returns a machine payload: `sql`, `password`,
  `saved_connection`, `check_will_warn`, `owner_dangerous_privs`, `next`

**The one-run rule**: the password is regenerated on every run — the SQL
handed to the DBA and the saved config must come from the **same** `--save`
run. Never preview first and save later.

Hand the SQL to a DBA, then:

```bash
pythia check   # goal: the object table, and NO yellow warning
pythia sql "select sys_context('userenv','proxy_user') proxy, user connected_as from dual"
# expected:  <AGENT> | <OWNER>
```

### Owner holding DBA? Trim it

A proxy session inherits the owner's power — an owner with DBA makes every
agent instance-wide DBA. `check` says so. The cleanup (run as a DBA,
**grant first, revoke second**, on dev first):

```sql
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE,
      CREATE PROCEDURE, CREATE TRIGGER, CREATE TYPE, CREATE SYNONYM
  TO app_owner;
ALTER USER app_owner QUOTA UNLIMITED ON users;
-- + explicit grants the code really uses (e.g. EXECUTE ON sys.dbms_crypto)
REVOKE DBA FROM app_owner;
REVOKE RESOURCE FROM app_owner;
```

Check first what the code touches outside its schema / in SYS:

```bash
pythia sql "select distinct referenced_owner from all_dependencies where owner='APP_OWNER' and referenced_owner not in ('SYS','PUBLIC','APP_OWNER')"
pythia sql "select referenced_name, count(*) n from all_dependencies where owner='APP_OWNER' and referenced_owner='SYS' and referenced_name like 'DBMS_%' group by referenced_name"
```

After the revoke: `pythia invalid` + `pythia errors` — grant exactly what
broke, never hand DBA back. Manual alternative:
[`examples/agent-user-setup.example.sql`](examples/agent-user-setup.example.sql).

## 4. Reading and understanding a schema

The principle: **ask the database, never a dump** — dumps drift (a real
audit: all types and packages missing, 89% of indexes).

```bash
pythia check                  # connectivity + object counts + privilege warning
pythia ls "PKG_%"             # find objects by LIKE pattern
pythia src PKG_ORDER --body   # source with the COMPILER's line numbers
pythia args P_CREATE_ORDER    # signature: names, order, types, defaults
pythia cols T_ORDER           # columns + types — anchor %TYPE/%ROWTYPE here
pythia ddl TABLE T_ORDER      # DDL via DBMS_METADATA
pythia grep "partner_id"      # search all PL/SQL source
pythia sql "select ..."       # free query — SELECT/WITH ONLY
```

Relationships and health:

```bash
pythia impact T_ORDER          # what depends on it — MANDATORY before any change
pythia deps PKG_ORDER          # what it depends on (--with-sys includes SYS)
pythia invalid                 # every INVALID object
pythia errors PKG_ORDER        # compile errors, line:column
pythia plscope T_ORDER         # exact identifier usages (PL/Scope)
pythia similar PKG_ORDER_LIST  # similarly named programs — the convention mine
```

Output: `--json` on every command; `--limit` / `--max-lines` / `--offset`
(0 = no cap); **every cut carries a marker** `-- truncated ...` — no marker
means you saw everything. Color is for humans only (`NO_COLOR` /
`FORCE_COLOR`); pipes and `--json` stay plain.

## 5. The write path: apply

**One door for writes**: `pythia apply` — never `sqlplus`, never SQLcl MCP
`run-sql`, never a driver script. DDL in Oracle **self-commits**: a snapshot
taken before the write is the only real undo there is.

```bash
pythia apply PKG_ORDER_BODY.sql            # preview: diff + impact + warnings + token
pythia approve a1b2c3                      # the DEVELOPER, at their own terminal
pythia apply PKG_ORDER_BODY.sql --confirm a1b2c3   # write exactly what was previewed
```

Steps, none removable: **snapshot → impact → preview → approve → apply →
verify → report**.

**Two steps, two people.** The preview ends by printing both lines: the
developer's `approve` and the agent's `apply --confirm`. The agent relays
both and stops. `approve` mints a one-time grant, and without it the confirm
is refused:

```
$ pythia approve a1b2c3

  Approving: PKG_ORDER (PACKAGE BODY) in APPDEV
  Impact: 12 dependent objects, 11 currently VALID
  Previewed 2026-08-27 14:02:11 on connection dev.

  Grant minted — single use, expires in 15 minutes.
  The agent may now run:  pythia apply <file> --confirm a1b2c3
```

- `approve` runs **only at a real console** and honours no `PYTHIA_CI`
  escape — it is the one command an agent cannot run. It touches no
  database, so a terminal with no connection configured can still approve
- The grant is single-use, expires after 15 minutes, and is bound to the
  connection the preview ran on — approving on `dev` does not approve
  `staging`. Expired ones are swept automatically; there is nothing to prune
- One statement per file; anonymous PL/SQL blocks are refused outright;
  unclassifiable statements are refused, never guessed
- The 6-hex token binds the write to the previewed content — if the file or
  the database object changed, the token is stale and you preview again
- Type-changing applies (function → procedure of the same name…) are
  refused at preview
- `--yes` skips both the stop and the separate approve — it is **the
  developer's flag**, and at a real terminal it *is* the approval act.
  Without a terminal attached (an agent driving the CLI) it is refused, as
  is any `policy set` to a looser value; real pipelines set `PYTHIA_CI=1`.
  The journal records how every write was authorized (`grant` / `yes`),
  when the grant was minted, and whether a TTY was present
- `journal restore` goes through the same gate, because it goes through the
  same write path
- The preview warns when a new object's name drifts from the project's
  naming conventions

**Exit codes are the verdict, machine-readable:**

| Code | Meaning | What an agent must do |
|---|---|---|
| `0` | clean | report success |
| `1` | refused | relay the reason verbatim, never route around it |
| `3` | **written but broken** | NEVER report success — show the errors and the printed restore command |

## 6. The journal — snapshots and restore

```bash
pythia journal list            # every entry, [applied] / [preview]
pythia journal show <id>       # metadata
pythia journal diff <id>       # before vs after
pythia journal export <id> --what before|after|restore
pythia journal restore <id>    # a WRITE — runs the same six steps, preview + approval
pythia journal prune           # drops preview-only entries; applied entries are always kept
```

A restore is a write like any other — no silent shortcut. If the object did
not exist before, restore means `DROP`, and the report says so plainly.

## 7. Policy, settings, conventions

### `.pythia/policy.json` — per-group write policy

```bash
pythia policy                        # effective policy + the honest rollback table
pythia policy set structural confirm # change one group
```

| Group | Default | Is rollback real? |
|---|---|---|
| `plsql_source` | `confirm` | **Yes — completely** (ALL_SOURCE) |
| `data_dml` | `deny` | **No.** After commit only Flashback Query remains. Revalidation checks the row set before the write; it is not an undo |
| `structural` | `deny` | **Almost never.** `DROP COLUMN` is permanent |
| `grants` | `deny` | Yes, but by hand |
| `session` | `allow` | Not needed |

The groups that cannot be snapshotted default to `deny` — and the refusal
says that, not "policy forbids it".

### `.pythia/settings.json`

```json
{ "plscope_on_apply": false }
```

Default **on**: every object applied through pythia compiles with PL/Scope,
so `pythia plscope` always has a complete index on the dev schema.

### Conventions — house style as config

```bash
pythia conventions --init    # writes the pair below; never overwrites
pythia conventions           # show what is in effect
```

- `.pythia/conventions.json`: naming patterns per object type. Every apply
  preview warns when a new object's name drifts from them. Style warns;
  policy is what blocks.
- `.pythia/conventions.md`: the same rules in prose, plus the ones no regex
  can express. `pythia-write` reads this before writing anything and treats
  it as outranking the generic patterns the pack ships with.

Replace the placeholder patterns with the real ones —
`pythia similar <A_TYPICAL_NAME>` shows what the schema already does, which
beats inventing a scheme. In `conventions.md`, write the cost of breaking
each rule, not just the rule: a consequence gets followed where an
instruction gets skipped. Commit both files so the team and every agent
session work from the same rules.
## 8. Exact non-ASCII literals: unistr

Raw non-ASCII literals break with client/DB charsets. The rule (enforced by
the `pythia-write` skill): **every non-ASCII literal goes through
`pythia unistr`**:

```bash
pythia unistr "Nhóm không được để trống"
# → unistr('Nh\00F3m kh\00F4ng \0111\01B0\1EE3c \0111\1EC3 tr\1ED1ng')

pythia unistr --loi "Bạn chưa nhập mã"
# → 'loi:'||unistr('B\1EA1n ch\01B0a nh\1EADp m\00E3')||':loi'

echo "text" | pythia unistr    # stdin works; no database needed
```

Single quote → `''` per SQL (never `\'` — ORA-01756), backslash → `\\`,
beyond the BMP → `\U` + 8 hex.

## 9. The agent skill pack

Eight skills, superpowers-style gates — not suggestions:

| Skill | Triggers when | Core job |
|---|---|---|
| `pythia-setup` | configuring a machine/project, connection failures, privilege warnings | connections, agent-user, SQLcl MCP |
| `pythia-spec` | a request leaves real decisions open | options + trade-offs to the developer BEFORE building; mid-build discoveries stop the build |
| `pythia-explore` | understanding anything in a schema | ask the database, never a dump |
| `pythia-impact` | **before** proposing any change | ≥10 dependents or cross-schema → show the developer first |
| `pythia-write` | writing/modifying PL/SQL once impact is known | copy conventions, anchor types to the DB, unistr |
| `pythia-apply` | a change is ready to reach the database | the developer sees the preview and approves in chat; exit 3 ≠ success |
| `pythia-review` | reviewing PL/SQL | the database's signals + seven antipatterns, line-anchored findings |
| `pythia-conventions` | a standards document or base schema whose style should be adopted | scan the names, verify coverage, write conventions.json + .md |
| `pythia-skill-author` | "make a skill for how we do X" | interviews + mines the live schema → a new skill in this pack's format |

Reads may flow through Oracle's SQLcl MCP server (optional, reads only):
`sql -mcp`, keep `-R 4`; it audits into `DBTOOLS$MCP_LOG`. **Writes never
go through MCP.**

## 10. Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `ORA-01017` on check | wrong user/password — after `agent-user`, re-read the one-run rule (§3) |
| `ORA-28000` account locked | failed attempts → `agent-user` emits `ALTER ... ACCOUNT UNLOCK` for the DBA, or `ALTER USER x ACCOUNT UNLOCK` |
| `ORA-01920` creating the user | user exists — ≥0.2.2 switches to the ALTER form automatically; run `agent-user` with the DB reachable |
| `ORA-01749` grant to yourself | the grants script runs as the schema being changed — run as SYSTEM/another DBA |
| `ORA-28150/28154` | missing `ALTER USER owner GRANT CONNECT THROUGH agent` |
| Skills doubled in the `/` menu | the pack exists in two places (global + project, or two dirs) → keep global, remove project copies; ≥0.2.4 avoids this |
| Skills not showing | the pack sits only in a project `.agents/skills` your Claude Code doesn't read → `pythia install -g` |
| Yellow warning from `check` | see §3 — proxy not used yet, or the owner over-granted |
| Token refused on `--confirm` | file or DB changed since the preview — previewing again is the design |
| `no developer approval is on file` | the developer has not run `pythia approve <token>` yet; relay the line and wait — retrying does not create approval |
| `That approval expired` / `already used` | grants are single-use and last 15 minutes — preview again, approve the new token |
| `approval was given on connection X` | approved against a different database — approve on the connection this session targets |
| `approve ... needs a real console` | an agent tried to mint its own approval; that is the gate working |
| Exit 3 after apply | written but new errors/invalids — read them, run the printed `journal restore` |
| Output cut short | a `-- truncated` marker is present — raise `--limit`/`--max-lines`, or `--offset` to continue |
| `--yes ... no terminal is attached` | an agent tried to self-approve — by design: preview, relay verbatim, stop; the developer approves, then `--confirm <token>` |
| `Loosening the write policy ... no terminal` | same design: hand the developer the printed `policy set` command to run themselves |

## 11. Optional: Claude Code permission settings and the session hook

The example settings now also carry a `SessionStart` hook running
`python -m pythia guide --brief`: ~15 lines injected once per session, which
is what makes skill routing deterministic — build requests reach
`pythia-spec` whether or not the agent felt like checking its skills that
day. Remove the `hooks` block if you prefer trigger-matching alone.

### The original section

Claude Code decides for itself whether to run a command, and in auto mode a
classifier makes that call per command, in context. Two things follow.

**Read commands prompt for nothing worth deciding.** Twenty-two pythia
commands cannot write — `sql` refuses anything that is not SELECT/WITH, and
the rest only read the data dictionary. Approving them one by one teaches a
developer to approve without looking, which is the opposite of what a
permission prompt is for.

**Writes get no extra pause.** In auto mode `pythia apply … --confirm` may
well be judged safe and run without stopping. pythia still required a
preview and a token, and the skills still require your approval in chat —
but the harness itself adds nothing.

[`examples/claude-code-settings.example.json`](examples/claude-code-settings.example.json)
addresses both. Copy it to `.claude/settings.json` (merge if you already
have one), then restart the session and check `/permissions`.

**pythia does not install this, on purpose.** It is another product's
security configuration, it applies to one agent out of the 77 the skill
pack supports, and — most of the reason — the two halves are not equally
strong:

| Half | Mechanism | Strength |
|---|---|---|
| `permissions.allow` | deterministic rule matching | reliable; the syntax matches what Claude Code itself writes when you click "always allow" |
| `autoMode.allow` / `soft_deny` | text fed to the auto-mode classifier | advisory — it shifts the odds, it does not decide |

So do not read the second half as "writes now always pause". The guarantees
are elsewhere and unchanged: the confirm token binds a write to a preview
you saw, `.pythia/policy.json` refuses whole groups outright, and the
agent's Oracle grants are the only layer that cannot be talked around.
Treat this file the way you treat
[`agent-user-setup.example.sql`](examples/agent-user-setup.example.sql) —
something the kit hands you, and you decide to run.

---

Security details, the dump-vs-database table, star history:
[README.md](README.md). Contributing — TDD, the bind-contract lint, the
skill lint: [CONTRIBUTING.md](CONTRIBUTING.md).
