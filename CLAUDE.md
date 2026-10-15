# CLAUDE.md — working on pythia itself

@AGENTS.md

The standing rules above are for any agent using pythia. This file adds what
a session changing pythia itself must do; CONTRIBUTING.md is the long form.

## Before calling a change done

- Run every suite — no database needed:
  `python tests/test_phase1.py`, `test_phase2.py`, `test_phase3.py`,
  `test_phase5.py`, `test_phase6.py`, `test_mcp.py`, `test_install.py`,
  then `python bench/bench.py --check`. TDD: the failing test comes first.
- CI runs Ubuntu, Windows and macOS on Python 3.9 and 3.13: build paths with
  `os.path.join` / `os.sep`, write shell strings with `as_posix()`, derive the
  interpreter from `sys.executable`, and use no stdlib API newer than 3.9.

## The contracts CI enforces

- **Queries:** every SQL statement lives in `queries/`, one per file, named
  binds only, the `-- Purpose / -- Binds / -- Returns` header, and an entry
  in `QUERY_BINDS`.
- **Skills:** `skills/<name>/SKILL.md`, `name` = folder, `description` opens
  with `Use when` / `Use before`, a `**Phase:**` line, at most 150 lines
  (detail in `reference/`); a new skill is declared in `EXPECTED`
  (tests/test_phase5.py), `.claude-plugin/marketplace.json` and the
  `using-pythia` routing table.
- **Docs never contradict code:** a behaviour change updates the docstring,
  README.md, README.vi.md, GUIDE.md, GUIDE.vi.md, the affected skill,
  SECURITY.md when a gate moves, and CHANGELOG.md — in the same change.
- **Versions** agree in pyproject.toml, plugin.json, marketplace.json,
  npm/package.json and `pythia.__version__` — and `site/index.html`, which
  also states the command, skill, query and lint-rule counts.

## Never

- Weaken a write-path invariant: snapshot before write, no silent restore,
  unrecognised statements refused, exit 3 for written-but-broken, a person's
  approval (or their lease) before a write. When touching hooks, tokens,
  leases or deploy, extend the adversarial tests in tests/test_phase6.py.
- Put real-world identifiers anywhere (hosts, schemas, tenants, credentials);
  examples use APP_OWNER, PKG_ORDER, T_ORDER. Everything shipped is English.
- Add a runtime dependency: stdlib + python-oracledb only.
- Run `npx skills remove` or field-test `pythia install` inside this repo —
  `skills/` is the source pack; use a scratch directory.
