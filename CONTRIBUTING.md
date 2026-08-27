# Contributing

Thanks for looking under the hood. This project runs on three enforced
contracts — break one and CI fails before a human ever reviews.

## Setup

```bash
git clone https://github.com/thaildhe172591/pythia && cd pythia
pip install oracledb          # only needed to talk to a real database
```

## Tests — no database required

```bash
python tests/test_phase1.py   # CLI foundation: config, resolution, output
python tests/test_phase2.py   # query library + bind-contract lint
python tests/test_phase3.py   # write path against a fake connection
python tests/test_phase5.py   # skill-pack lint + README contract
```

The write path is tested with a fake connection that records every statement,
which is how the safety properties are *proven*, not assumed: the snapshot
lands on disk before any execute (even when the execute explodes), `deny`
touches nothing, a stale token never writes, a broken apply exits 3.

CI runs all four suites on Ubuntu, Windows and macOS, Python 3.9 and 3.13.

The benchmark ([BENCHMARK.md](BENCHMARK.md), method in
[bench/README.md](bench/README.md)) runs the same suites plus direct probes
of every documented safety gate; CI fails any push where reliability drops
below 100, and re-scores the scoreboard automatically when a new version
lands on main:

```bash
python bench/bench.py --check   # what CI runs
```

## Contract 1 — every SQL statement lives in `queries/`

One statement per file, named binds only, a three-field header
(`-- Purpose / -- Binds / -- Returns`), and an entry in `QUERY_BINDS` in
`scripts/pythia.py`. The lint fails on drift in either direction. See
[`queries/README.md`](queries/README.md).

## Contract 2 — skills stay lean and triggerable

`skills/<name>/SKILL.md`: frontmatter `name` matches the folder, `description`
opens with the trigger condition (`Use when ...` / `Use before ...`), body
within ~150 lines, long material in `reference/`. The pack's skill list is
fixed in `tests/test_phase5.py` — adding a skill means declaring it there
first (write the test change, watch it fail, then write the skill).

## Contract 3 — documentation never contradicts code

The original tool this project grew from documented a `--write` flag its code
refused. That contradiction is the founding bug: if you change behavior,
change the docstring, the README and the affected skill in the same PR. The
rollback-honesty table exists in three places (CLI `policy`, README,
`pythia-apply` skill) and must stay identical.

## Hard rules

- **No real-world identifiers.** No hosts, IPs, credentials, schema or tenant
  names from any actual system — in code, examples, tests, comments, or
  commit messages. Examples use `APP_OWNER`, `PKG_ORDER`, `T_ORDER`.
- **No new runtime dependencies.** Core is stdlib + `oracledb`. A PR adding a
  dependency needs a very good story.
- **English** for everything shipped (code, comments, skills, docs).
- **TDD is the workflow**: failing test first, minimal change, suite green.
  Pure functions for anything that decides or formats — that is what keeps
  the no-database test suite honest.
- **Write path invariants are non-negotiable**: snapshot before write, no
  silent restore, unrecognized statements refused, exit 3 for
  applied-but-broken.

## Field-testing inside this repository — one footgun

`npx skills` treats a root `skills/` directory as an *install location*, so
running `npx skills remove` in this repo deletes the **source** skill pack,
not just installed copies. Everything is tracked, so recovery is
`git restore skills/` — but prefer field-testing installs in a separate
directory, and treat `.agents/`, `.claude/` and `skills-lock.json` here as
disposable artifacts (they are gitignored).

## Tests run on three platforms — write them that way

CI runs every suite on Ubuntu, Windows and macOS. Three separate failures in
this repo came from tests that quietly assumed Windows:

- Paths: build them with `os.sep` / `os.path.join`. A literal `C:\dir` is
  not a path on POSIX, and `:` is the PATH separator there, so it splits.
- Behaviour that differs by platform: assert the difference explicitly
  (`"x" if os.name == "nt" else "y"`), never just the half you can see.
- The interpreter's own name: `python` on Windows, usually `python3`
  elsewhere. Derive it from `sys.executable`.

A green local run on Windows proves a third of what CI will.

## Adding a query

1. Write `queries/<name>.sql` with the header and named binds.
2. Declare its binds in `QUERY_BINDS`.
3. Wire a command or use it from an existing one; reuse `emit_table`/`clip`.
4. Run the suites; smoke-test against a real dev schema before the PR.

## Testing the skills themselves

`tests/*.py` prove the CLI. The skills are shipped artifacts too, and the
only thing that proves them is an agent's behaviour — so
[`tests/agent-conformance.md`](tests/agent-conformance.md) is a set of chat
scenarios, each written to tempt an agent into breaking exactly one rule,
with observable PASS/FAIL criteria. Run it after any change to `skills/`
or to the approval gates.

## Adding or changing a skill

1. Declare it in `EXPECTED` in `tests/test_phase5.py` (new skills).
2. Follow the format of the existing pack; `pythia-skill-author` documents it.
3. Verify triggering in a fresh agent session — a skill that does not
   trigger does not exist.

## Pull requests

- One concern per PR, tests included, all four suites green locally.
- Explain *why* in the commit body; the repo's history is written as prose
  and reviewers read it.

## Releasing

Versions live in four files and must agree: `pyproject.toml`,
`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
`npm/package.json` (`tests/test_install.py` enforces it). To release:

1. Bump the version in all four, update `CHANGELOG.md`, merge to `main`.
2. `git tag vX.Y.Z && git push origin vX.Y.Z` — the `release` workflow
   checks the tag against the manifests, runs the suites, then publishes
   `pythia-plsql` to PyPI and npm.

One-time setup by a maintainer:

- **PyPI**: add a trusted publisher (pypi.org → account → Publishing →
  add a *pending* publisher for project `pythia-plsql`, repository
  `thaildhe172591/pythia`, workflow `release.yml`, environment `pypi`).
  No token is stored anywhere.
- **npm**: `npm token create` (automation token) → repository secret
  `NPM_TOKEN`. The very first `npm publish` of a name cannot use OIDC,
  so the token path stays.
