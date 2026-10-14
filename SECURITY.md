# Security

## Reporting a vulnerability

Use GitHub's [private vulnerability reporting](https://github.com/thaildhe172591/pythia/security/advisories/new)
rather than a public issue. Include what you ran, what happened, and what you
expected. Expect a first reply within a week.

## What this tool can do, stated plainly

pythia connects to an Oracle database with credentials you supply and can
issue `CREATE OR REPLACE` against it. That is the product, not a side effect.
Anyone evaluating it should weigh that directly, so here is where the risk
actually sits and what contains it.

**The database account is the only boundary that cannot be argued with.**
Policy files, skills and confirmation tokens are application-side; a
determined agent or a mistaken human can route around any of them. An Oracle
grant cannot be routed around. Give the agent its own credential, scoped to
one development schema, via proxy authentication — see
[`examples/agent-user-setup.example.sql`](examples/agent-user-setup.example.sql).
`pythia check` warns when the session holds more power than the task needs.

**Never point it at production.** This is Oracle's own guidance for LLM
access and it is ours.

Everything in this tier — the three-role layout, the proxy pattern, the
grants we suggest — is the author's guidance, not a requirement the tool
imposes or takes responsibility for. It is one additional hardening layer
at the role tier; your site's security model belongs to your DBA.

### The layers, and what each is worth

| Layer | Stops | Can be bypassed by |
|---|---|---|
| Oracle grants | anything outside the granted schema | nothing in this tool |
| Policy (`.pythia/policy.json`) | whole statement classes; DML, DDL and grants are `deny` by default | editing the file, deliberately |
| Confirmation token | applying content that differs from what was previewed | not applying at all |
| Approval grant | an agent completing a write no human approved | fabricating the grant file, faking a console, or piping a hand-made hook payload into `approve --hook`, deliberately — the `policy.json` tier |
| Card check in the chat door | a human approving the agent's paraphrase instead of the preview | nothing short of the above — the hook mints only when the question carries pythia's own card verbatim, and refuses answers pre-filled in the tool input |
| MCP approver (`pythia_approve`) | an approval given without a person seeing pythia's card | an Elicitation hook the developer configured to auto-accept, Codex's `danger-full-access`, or Claude Code's `dontAsk` (which denies the tool instead) |
| Autopilot lease | a delegation wider than the developer approved | editing `lease.json` by hand, deliberately — the `policy.json` tier; the lease never covers a protected connection, destructive statements the card did not include, or lint errors |
| PreToolUse guard | sqlplus/SQLcl writes that skip the write path, forged `approve --hook` calls | obfuscating the command past a regex — it is a seatbelt for a well-meaning agent, not a sandbox |
| TTY requirement | a headless agent self-approving `--yes` or loosening policy | `PYTHIA_CI=1`, deliberately, in a real pipeline |
| Snapshot + journal | losing the previous version of PL/SQL source | nothing — it runs before every write and no flag disables it |

Honest limits: a snapshot restores **source**. It does not restore rows, a
dropped column, or a revoked grant. The rollback table in the README and in
`pythia-apply` says which group is genuinely reversible, and the tool refuses
the groups that are not rather than implying otherwise.

The approval grant has two doors since 0.10.0, and each has an honest
reading. The console door assumes a developer with a terminal on the machine
holding the repo. The chat door (`AskUserQuestion` + the `PostToolUse` hook)
closes the remote-developer gap for Claude Code: the answer the hook reads was
written into the payload by the client, not the agent, and the hook refuses
unless the question carried pythia's own card verbatim — so what is approved
is the preview, not a paraphrase. Its limit: the hook is a process the agent's
shell could also start. An agent that pipes a hand-made payload into `pythia
approve --hook` has forged an approval — the same tier as faking a console or
writing the grant file by hand, and `pythia install` writes a deny rule for
that command shape into the Claude Code settings outright. Since 0.14.0 (Codex) and 0.15.0 (Claude Code) the
MCP approver is a third door: its tool is marked `requiresUserInteraction`,
so Claude Code prompts for it in every permission mode but `dontAsk`, and the
elicitation shows pythia's own card — which also makes it immune to the
settings that make `AskUserQuestion` return empty (`pythia doctor` lists
them). What the grant raises is the
*bar*, not a wall: it turns a write an agent could complete on its own
authority into one that takes deliberate forgery, which is the same tier as
editing `policy.json`.

### Autopilot, and why it is not a hole in the gate (0.15.0)

A lease is the same human act as an approval, given once for a scope it
names: one connection, the statement groups on the card, a duration of at
most 24 hours and a write budget. It starts only through an approval door —
the console, the chat card, the MCP elicitation — never from the agent's own
command (`autopilot on` refuses without a console). It narrows what the agent
may do without asking; it widens nothing else:

- protected connections (`"protected": true`, `"env": "prod"`) are refused
  outright, whatever the card says;
- destructive statements — DROP, TRUNCATE, ALTER ... DROP, renames, SET
  UNUSED, REVOKE, UPDATE/DELETE without WHERE — stay one-approval-at-a-time
  unless the developer asked for them by name, and the card says they
  cannot be undone;
- a file with lint errors is not landed, and the security-relevant checks
  (GRANT ... TO PUBLIC, ANY privileges, grant options, powerful roles,
  always-true WHERE clauses) are hard-coded as destructive, so no lint
  setting — `conventions.json` is a file agents are told to edit — can let a
  lease land them;
- a DML statement touching more than 1,000 rows is shown to a person first;
- objects that were VALID going INVALID after any write — PL/SQL, DDL or
  grants — or five failed writes in a row, pause it until the developer
  looks;
- a connection marked protected after the lease began stops being covered
  at once; environment-variable credentials can never be leased;
- every write still snapshots, journals and verifies, and `autopilot
  report` lists each one with its undo.

A lease overrides a `deny` policy only for the groups its card names and
only while it is active — the card says so next to the group. That is the
point of approving a card rather than editing a file: the developer reads
the consequence at the moment they grant it.

A deploy approval covers the release as a whole and nothing else: its card
shows every statement in full (with its row set for DML), each statement's
approval is bound to that deploy and refused on its own, every statement is
checked against the preview before the first one runs, and a stop withdraws
whatever the approval had not used yet.

### The hooks are seatbelts, not a sandbox (0.15.0)

The PreToolUse hook allows pythia's read-only commands only as one plain
invocation — no `;`, `&&`, pipes, redirections, `$(...)`, backticks or `#`
(bash and Python's shlex disagree on where a comment starts, which a review
showed can smuggle a second command) — so an allowed command cannot carry a
second one. It classifies the command with the CLI's own parser, so an
option value never passes for a subcommand, and the CLI accepts no
abbreviated flags (`--ho` is an error, not `--hook`). It allows only a bare
`pythia`, an interpreter by name with `-m pythia` (and no `pythia.py` in the
working directory to shadow it), or a path to the very file running the
hook — not a script an agent wrote and named pythia. It allows a write only when a
live grant or lease already exists, which `apply` re-verifies itself. Its
deny on sqlplus/SQLcl writes is pattern matching: a determined agent can
phrase a command it does not recognise. The account's Oracle grants remain
the only boundary that cannot be argued with.

Row-set revalidation, added in 0.9.0, brings a limit of its own worth naming:
previewing a `data_dml` statement now runs a `SELECT` derived from that
statement, so a predicate calling a function executes that function at preview
time — before anyone has approved anything. This opens no trust boundary the
statement does not open already (the same file is asking to run its own DML,
and only a `SELECT` is built from it), but it is a new moment, and the preview
printing the whole statement is what makes it visible. The fingerprint itself
tracks *which rows* are in the set, by ROWID; it does not notice a value
changed in place inside a row that stays in the set.

## Credentials

`.pythia/connections.json` holds them in plaintext and is gitignored by the
scaffold. `PYTHIA_USER` / `PYTHIA_PASSWORD` / `PYTHIA_DSN` are read from the
environment if you prefer. pythia never transmits credentials anywhere except
to the database you configured, and never writes them to the journal.

## About the "high risk" rating on `pythia-apply`

Skill marketplaces score skills by scanning their text. `pythia-apply` scores
worst of the seven, and the substance of that is fair: it is the skill that
authorizes writing to a database, and it deserves more scrutiny than the six
that only read.

The specific words that trigger it are worth knowing, though, because every
one of them appears in a prohibition:

- `DROP` — in "policy will refuse it", and in the row explaining that
  `DROP COLUMN` is permanent
- `bypass` — in the section heading **"Never bypass the write path"**
- `token`, `credential` — the confirmation mechanism and the warning about
  over-privileged accounts

A keyword scanner cannot tell an instruction from a ban on that instruction.
We will not reword safety rules to score better; the rules are written to be
read by an agent about to modify someone's database, and that audience comes
first. Read [the skill](skills/pythia-apply/SKILL.md) — it is 120 lines, and
it argues against nearly everything it mentions.
