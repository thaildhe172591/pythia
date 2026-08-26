# Changelog

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
