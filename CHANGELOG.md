# Changelog

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
