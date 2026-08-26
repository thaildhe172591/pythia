# Working in this repository / with this tool

pythia is a CLI + skill pack for developing PL/SQL on Oracle Database. If you
are an AI agent, these are the standing rules; the `skills/` directory
carries the full workflows.

## Invocation

Run as `pythia <command>` when installed from pip (`pip install
pythia-plsql`), or `python scripts/pythia.py <command>` from a clone. Every pythia output prints follow-up commands in the correct,
paste-able form — prefer running exactly those. `--json` gives structured
output on any command.

## Standing rules

1. **Ask the database, never the dump.** Repo exports drift (a real audit:
   all types and packages missing, 89% of indexes). Use `ls / src / args /
   cols / ddl / grep / plscope` against the live schema.
2. **Impact before change.** Run `pythia impact <OBJECT>` before proposing
   any modification. Ten or more dependents, or any cross-schema dependent:
   show the developer the list before writing code.
3. **Writes go through `pythia apply` only.** Never `CREATE OR REPLACE`
   through SQLcl MCP `run-sql`, `sqlplus`, or a driver script — those have
   no snapshot, no verify, no journal.
4. **The developer sees every preview.** Relay apply's diff, impact line and
   warnings verbatim; the preview ends your turn. `--confirm` only after
   the developer's approval arrives as a new message. `--yes` and loosening
   `policy set` are the developer's actions — the CLI refuses them without
   a terminal.
5. **Scope is what was asked.** Dependents that break are reported with a
   proposal, never auto-fixed.
6. **Exit codes are the verdict:** `0` clean · `1` refused (relay the reason,
   do not route around it) · `3` written but broken — never report success;
   show the errors and the printed restore command. Never read `$?` through
   a pipe: `pythia … | tail` returns tail's code, always 0.
7. **Truncation is always announced.** If output lacks a truncation marker,
   you saw everything; if it has one, say so or fetch the rest.
8. **Restores are writes.** `journal restore` goes through the same preview
   and approval as any apply.
9. **House style outranks generic style.** If `.pythia/conventions.md`
   exists, read it before writing any object; `pythia conventions` shows the
   machine-checked naming patterns, and apply previews warn on drift.

## Where things are

- `scripts/pythia.py` — the CLI (stdlib + python-oracledb, thin mode)
- `queries/*.sql` — every SQL statement the tool runs, reviewable in isolation
- `skills/` — the seven workflow skills (`pythia-apply` is the write gate)
- `examples/` — connection config and the least-privilege agent-user setup
- `tests/` — four suites, no database required; run them before claiming done

## Contributing changes to pythia itself

Follow [CONTRIBUTING.md](CONTRIBUTING.md): TDD, the bind-contract lint, the
skill lint, and documentation that never contradicts code.
