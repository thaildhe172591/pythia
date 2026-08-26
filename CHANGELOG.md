# Changelog

## 0.3.3 — 2026-08-27

- **pythia-explore now fires when a `.sql` file looks like the answer.**
  Conformance scenario A1 failed against a real agent: asked to explain a
  procedure "based on this repo file", it read the file and never touched
  the database. The skill body already argued against exactly that — but
  the trigger described "understanding an Oracle schema", and the prompt
  read as file-reading, so the skill never loaded. The description now
  names the trap (a file, dump, export or migration script that appears to
  answer already, including one the developer points at), and the body
  says what to do when handed one: read it, then ask the database, and
  report any difference first.

## 0.3.2 — 2026-08-27

Both fixes come from running the kit against a real 1,516-object schema.

- **The approval gate held on POSIX but not on Windows.** `NUL` is a
  character device, so `isatty()` answers True for a child launched with
  `stdin=DEVNULL` — an agent spawning pythia that way sailed through both
  0.3.0 gates and could loosen policy and self-approve writes. The gate now
  also asks `GetConsoleMode`, which only a real console answers, and an
  end-to-end test spawns a `stdin=DEVNULL` subprocess to prove it.
- **The report promised an undo the tool would refuse.** Undoing a CREATE
  is a DROP, DROP is `structural`, and `structural` is `deny` by default —
  so the printed `journal restore` line could not run. It now says so on
  the spot, and names the command the developer would have to run first.

## 0.3.1 — 2026-08-26

- Refusals that need no database fire before any connection is opened: a
  non-SELECT `sql` statement and a headless `--yes` are refused with their
  real messages even when the connection is unreachable or locked — found
  when ORA-28000 masked both during a field test of the 0.3.0 gates.

## 0.3.0 — 2026-08-26

- **The developer's approval is now enforced by the CLI, not just asked of
  the agent** (field report: an agent self-approved writes and widened its
  own scope). Without a terminal attached, `apply --yes` is refused, and
  so is any `policy set` to a looser value — tightening stays free. A
  human at the keyboard is unaffected; real pipelines set `PYTHIA_CI=1`.
  Every journal entry now records how the write was confirmed (`token` /
  `yes`) and whether a TTY was present.
- Skills hardened to match: the preview ends the agent's turn (`--confirm`
  only after the developer's approval arrives as a new message), and
  scope is the developer's sentence — dependents that break are reported
  with a proposal, never auto-fixed.

## 0.2.4 — 2026-08-26

- **Global-first skills** (field report): some Claude Code versions never
  read a project's `.agents/skills/`, so the project copy was invisible
  while the global pack showed — or both showed, duplicated. Now:
  `pythia install -g` installs the pack machine-wide; a project
  `pythia install` that finds the global pack skips the skills step
  entirely (config scaffold still runs). The no-Node fallback targets
  `.claude/skills/` — the directory Claude Code reliably reads in both
  scopes — and cleans stale `.agents` pack copies and `plsql-*` names.

## 0.2.3 — 2026-08-26

- Duplicate-skill fix covers the npx path too: unattended `pythia install`
  passes `-a universal` (one copy in `.agents/skills/` serves every
  agent), and stale `plsql-*` copies are cleaned after npx installs as
  well, not only in the no-Node fallback. The interactive agent picker is
  unchanged.

## 0.2.2 — 2026-08-26

- `agent-user` asks the database before writing SQL: an agent user that
  already exists gets the `ALTER ... ACCOUNT UNLOCK` form (CREATE would be
  ORA-01920), and the owner's grants are inspected up front — the output
  states whether `check` will pass clean or still warn, before the DBA
  runs anything. Offline (no connection) falls back to CREATE plus the
  ALTER fallback as a comment.

## 0.2.1 — 2026-08-26

- `agent-user` speaks agent: `--json` returns owner/agent/password/sql/
  saved_connection in one self-consistent payload; the text output warns
  that the password is regenerated per run (one `--save` run, never
  preview-then-save); the setup skill and README teach that flow.

## 0.2.0 — 2026-08-26

- **Skills renamed `plsql-*` → `pythia-*`** for recognizability. The no-Node
  installer cleans stale `plsql-*` copies; npx users: `npx skills remove`
  the old names once.
- **No more duplicate skills**: the bundled-pack fallback installs to
  `.agents/skills/` only — Claude Code reads both conventional roots, so
  the second copy in `.claude/skills/` doubled every skill in the menu.
  The copy merges (your other skills are untouched), and connections.json
  is never overwritten, as before.
- **PL/Scope on apply** (from IDEAS): the write session compiles
  `plsql_source` with `plscope_settings='IDENTIFIERS:ALL, STATEMENTS:ALL'`,
  so applied objects always carry the semantic index `pythia plscope`
  reads. Opt out: `{"plscope_on_apply": false}` in `.pythia/settings.json`.
- **Proxy-aware privilege warning** (from IDEAS): a proxy session
  inheriting `ANY` privileges is told the owner's grants are the problem;
  a clean proxy session warns not at all.
- **`journal prune`** (from IDEAS): removes preview-only entries; applied
  entries — the real snapshots — are always kept.

## 0.1.3 — 2026-08-26

- `pythia agent-user` — prints the least-privilege proxy-user SQL for the
  current connection's schema (generated password, no DBA/RESOURCE/ANY);
  `--save` adds the credential to connections.json as `<conn>_agent` and
  makes it the default, owner entry untouched.

## 0.1.2 — 2026-08-26

- `pythia unistr` — exact Oracle literals for Vietnamese/non-ASCII messages
  (`--loi` wraps in the loi:...:loi error format); the write skill now
  requires it for non-ASCII literals.

## 0.1.1 — 2026-08-26

- Logo on the README, the PyPI page and a dedicated npm README.

## 0.1.0 — 2026-08-26

First public release.

- **Read commands**: `check`, `ls`, `src` (compiler line numbers), `args`,
  `ddl`, `cols`, `grep`, `sql` (SELECT/WITH only) — capped output with
  explicit truncation markers, `--json` everywhere.
- **Understanding commands**: `deps`, `impact` (with VALID summary),
  `errors` (line:col), `invalid`, `plscope` (exact identifier usages and
  per-table statement lookup), `similar` (convention mining by name tokens).
  Every SQL statement lives in `queries/` under a bind-contract lint.
- **Write path**: `apply` running snapshot → impact → preview → apply →
  verify → report, gated by `.pythia/policy.json`; content-bound 6-hex
  confirm token; journal with runnable restores; `policy` and `journal`
  commands; exit codes `0/1/3` with `3` = applied-but-broken; honest
  rollback table; anonymous blocks and unclassifiable statements refused.
- **Skills**: seven-skill pack (`setup`, `explore`, `impact`, `write`,
  `apply` gate, `review`, `skill-author`) in the `npx skills` layout, with
  a lint enforcing frontmatter, trigger-first descriptions and line budgets.
- **Security**: least-privilege proxy-authentication setup example,
  privilege warnings in `check` and previews, credentials never tracked.
- **Terminal**: colors and banner for humans (NO_COLOR/FORCE_COLOR
  respected), plain text for pipes and agents.
- **Install**: `npx pythia-plsql` is the one-command path — a
  dependency-free npm wrapper (`npm/`) that finds Python, pip-installs the
  package, and hands off to `pythia install` with the skills CLI's own
  interactive agent picker at a TTY. `pip install pythia-plsql` ships the whole kit — CLI plus
  `queries/` and `skills/` as package data. `pythia install` scaffolds
  `.pythia/connections.json` (never touching an existing one) and installs
  the skills via `npx skills add` (`--source` for internal mirrors), falling
  back to copying the bundled pack when Node.js is absent.
- **Platforms**: Windows, macOS, Linux, WSL — CI matrix on all three OS
  families, Python 3.9–3.13, no runtime dependency beyond `python-oracledb`.

Known gaps, tracked for next releases: SQLcl
detection/adapter phase, `inherit` in connection entries, journal pruning.
