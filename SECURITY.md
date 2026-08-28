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
| Approval grant | an agent completing a write no human approved | fabricating the grant file or faking a console, deliberately — the `policy.json` tier |
| TTY requirement | a headless agent self-approving `--yes` or loosening policy | `PYTHIA_CI=1`, deliberately, in a real pipeline |
| Snapshot + journal | losing the previous version of PL/SQL source | nothing — it runs before every write and no flag disables it |

Honest limits: a snapshot restores **source**. It does not restore rows, a
dropped column, or a revoked grant. The rollback table in the README and in
`pythia-apply` says which group is genuinely reversible, and the tool refuses
the groups that are not rather than implying otherwise.

The approval grant has its own honest limit: it assumes a developer with a
terminal on the machine holding the repo. A purely remote developer — the
agent on a server, the human only in chat — has no clean path today. That is
a known gap, stated rather than papered over; closing it needs an approval
channel that travels over the agent's own protocol, which is designed but not
yet shipped. And what the grant raises is the *bar*, not a wall: it turns a
write an agent could complete on its own authority into one that takes
deliberate forgery, which is the same tier as editing `policy.json`.

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
