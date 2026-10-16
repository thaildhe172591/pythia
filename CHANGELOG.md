# Changelog

## 0.15.9 — 2026-10-16

From the 0.15.8 field test: a project upgraded with `pip install -U` but not
re-installed got two `PYTHIA HARNESS ACTIVE` lines at session start, one from
the stale-block notice and one at the head of the brief.

- **One header.** The brief's own header comes first. Under it, a `NOTE` line
  says CLAUDE.md's block is from an older pythia, that these rules win where
  the two differ, and that `pythia install` refreshes it. The note uses the
  same label column as `RUN` and `STATE`.

## 0.15.8 — 2026-10-16

From two field reports on fresh machines. `pythia install` left a `doctor`
failure blamed on the hookify plugin. Then, in the Claude desktop app, both
chat doors refused a developer who did approve: `pythia_approve` came back
"declined" with no dialog shown, and AskUserQuestion's Approve was rejected
as pre-filled.

- **AskUserQuestion is the first door on Claude Code.** It is Claude Code's
  own dialog and shows in every client: terminal, desktop app, web and
  phone. Since Claude Code marks it `requiresUserInteraction`, no hook or
  allow rule answers it. The order is AskUserQuestion, then the
  `pythia_approve` tool (MCP, where Codex starts), then `pythia approve
  <token>` at the developer's terminal. Every failure that is not the
  developer's own Reject says so and names the next door, and the agent goes
  on without asking. The preview, deploy and lease requests print one list
  (`door_lines`). `grant_refusal`, the brief guide, the CLAUDE.md and
  AGENTS.md blocks, AGENTS.md rule 4 and the skills give the same order.
- **The chat door counts the answer the dialog writes.** Claude Code
  2.1.301 drops any `answers` the model wrote before the dialog shows, then
  writes the person's choice into `tool_input.answers`. pythia read that as
  pre-filled and refused every real Approve. The hook now reads the model's
  own call from the session transcript (`wireToolInputs`, by
  `tool_use_id`, else by the questions). Answers in that call are refused as
  the agent's; an answer it cannot trace sends the agent to the next door.
  A dialog that closed itself while nobody was at the keyboard
  (`afkTimeoutMs`) is no answer, and neither is a typed reply instead of a
  choice. Each is named.
- **A client that declines the MCP prompt unseen is named as such.** A host
  with no elicitation UI answers every prompt "decline" at once. pythia then
  told the agent the developer had declined, to stop and ask what should
  change. Now a non-accept back in under 1.5 s is reported as the client's:
  nothing is minted, and the agent moves to the next door. A slower decline
  is still the developer's no.
- **doctor reads only the plugins Claude Code runs.** It scanned every
  `hooks.json` under `~/.claude/plugins`, including the marketplace clone,
  which holds the source of every plugin the marketplace lists. With the
  official marketplace, that meant hookify and claude-security, installed or
  not. Now a plugin counts when `installed_plugins.json` lists it (else the
  newest copy in `cache/`) and `enabledPlugins` leaves it on: user, then
  project, then local settings; no mention means on unless the manifest
  says `defaultEnabled: false`. Hooks come from `hooks/hooks.json` and
  `plugin.json`'s `hooks` (paths or inline objects), and
  `CLAUDE_CODE_PLUGIN_CACHE_DIR` is honoured.
- **Only dontAsk fails the chat door.** An allow rule, a skill's
  `allowed-tools` or a hook catching AskUserQuestion once answered it
  before the developer saw it. Current Claude Code requires the dialog
  anyway, so these are warnings now. `dontAsk`, which denies both chat
  doors, stays a fail. The session's `approval door` line orders the doors
  by what is wired: AskUserQuestion leads where pythia's answer hook is
  wired, `pythia_approve` leads where it is not, and `dontAsk` leaves the
  terminal.
- **An older CLAUDE.md block is superseded at session start.** After `pip
  install -U` and before `pythia install` runs again, a project's CLAUDE.md
  still carries the old rules, the old door order among them. Session start
  now notices that the block differs from this version's, prints the
  current brief, and says which one wins.
- **pythia lands on PATH when an agent installs it (Windows).** With
  nobody at the console to answer, `pythia install` used to leave the
  scripts directory off PATH, so `pythia` never worked in the developer's
  own terminal. Now it adds the directory that holds `pythia.exe` to the
  *user* PATH. A person at the console is still asked, and `--no-path` skips
  it.
- **The website explains who it is for, and answers back.** A section per
  agent shows what `pythia install` wires for Claude Code, Codex and any
  other agent, side by side (11, 7 and 4 of the same 11 pieces). A
  dependency graph of the sample schema prints the `impact` of whichever
  object is picked. The data engineer's card shows `explain`, `stats` and
  `comments`. "Try the guard" prints the verdict the PreToolUse hook and the
  CLI give for seven commands, and the skills section routes a request the
  way `using-pythia` does. The ambient layers (vocabulary bands, tool calls,
  a row pipeline, the suites' own test names) pause off screen and with
  *Pause motion*, and none run under reduced motion. Benchmark numbers on
  the page are now 0.15.7's.
- `bench/README.md` counted 15 automated scenarios of 23; `agent_bench.py
  --list` has 18 of 27, with G3 and G4 also graded by hand.

## 0.15.7 — 2026-10-15

From the 0.15.6 field test, two Claude Code sessions in a fresh project:

- **`--help` reads.** `… -m pythia --help` got a permission prompt: help
  exits inside the parser, before any command runs, so the guard could not
  classify it. An exit 0 from the parser is help and nothing else, and now
  counts as a read; parser errors (exit 2) still fall to the client's
  prompt, and the forged-approval deny still runs first.
- **Lint says how to count comments kept in another file.** The agent
  linted a change file by file, got `comment-missing` for a table whose
  COMMENT ON lived in `02_comments.sql`, and could only guess. One file
  with comments missing now adds a hint (`hint` in `--json`) naming the
  run that counts them — the change's files, or the folder, together.
  The brief guide in CLAUDE.md, AGENTS.md rule 9, pythia-table and GUIDE
  say to lint a change's files in one run.

## 0.15.6 — 2026-10-15

From the 0.15.5 field test: a real Claude Code session read the `RUN` line
and, to shorten the long launcher, wrote `PY="…/venv/bin/python3"; "$PY" -m
pythia connections`. A variable and a `;` make a compound command, which the
guard rightly leaves to the client's prompt — so the first read cost a
permission round trip before the agent retyped it plainly.

- **The `RUN` line says how to type the spelling:** as written, one command
  per call, and why — a shell variable or `;` makes a compound command the
  guard asks about. AGENTS.md and the pythia-setup reference say the same.
  The guard itself is unchanged.

## 0.15.5 — 2026-10-15

From the 0.15.4 field test: a real Claude Code session in a project whose
pythia lives in a venv that is not on PATH. The hooks ran, but every command
the harness named for the agent — `pythia guide`, `pythia connections`,
the install's fallback `python3 -m pythia` — started nothing or the system
interpreter, which has no pythia. The agent got `command not found`,
concluded the hooks were off, and stopped at Learn.

- **Printed commands name what runs here.** The bare `pythia` only when PATH
  finds this very install; `python3 -m pythia` only when PATH's `python3` is
  this interpreter (a venv's python is a symlink to the base one, so paths
  are compared as spelled, not resolved); otherwise this interpreter by
  absolute path, `"…/venv/bin/python3" -m pythia` — the launcher the hooks
  already use. The install's PATH hint says the same. It also stops turning
  `python3.9` into `python3` (Path.stem took ".9" for an extension).
- **The session preamble says how `pythia` is spelled here.** CLAUDE.md is
  shared, so it says `pythia`; when that is not what runs on this machine,
  the SessionStart hook adds a `RUN` line naming the spelling, and spells its
  own pointers that way. It now names the approval door when the MCP
  approver lives in Claude Code's local scope, not only in `.mcp.json`.
- **The guard lets reads through in that spelling.** The PreToolUse hook
  also allows read-only commands run by the hook's own interpreter at its
  absolute path (with `-m pythia`, or with this very pythia.py), or by the
  `pythia` script beside it. Nothing else by path: a look-alike, another
  directory or a symlink to the interpreter gets the client's prompt.

## 0.15.4 — 2026-10-15

From the 0.15.3 field test: `git status` in the sample projects listed
`.pythia/`, one `git add -A` away from committing the passwords — while
GUIDE.md and SECURITY.md said the file was "gitignored by the scaffold".

- **install keeps `.pythia/connections.json` out of every clone.** It writes
  the path into the project's `.gitignore` (created if missing, your bytes
  kept) unless a rule there already ignores it. A rule only this machine has
  — `.git/info/exclude`, a personal excludes file — does not count, and a
  later `!` negation is overridden by appending the rule after it. Outside a
  repository the line is written anyway, so a later `git init` starts safe.
  Re-running install on an existing project adds it.
- **When git already tracks the file**, install warns and `pythia doctor`
  fails a new **credentials** row: a rule cannot untrack a committed file,
  so both name `git rm --cached .pythia/connections.json`, a commit, and
  changing the passwords the history keeps. doctor warns when the file is
  not ignored or ignored only on this machine, and is ok when a shared
  `.gitignore` rule covers it.
- doctor's closing line no longer says every `fail` stops approvals — a
  tracked password file does not; it says each `fail` line names its fix.
- **A project website** at https://thaildhe172591.github.io/pythia/ —
  `site/`, one static page in English with a Vietnamese toggle, published
  by the `pages` workflow on every change to `site/` on main. It states the
  release, the counts and every skill by name; `tests/test_install.py`
  fails when any of them drifts from the code. It moves: a replay types one
  write end to end — impact, lint, the preview, the approval, the write,
  the verdict — while a track lights each of the six steps; the recorded
  terminals stream in, the hero prompt keeps typing the commands an agent
  runs, numbers count up and blocks rise as they arrive. The page as served
  is the finished state; with reduced motion none of it runs, and "Pause
  motion" holds whatever is mid-flight.

## 0.15.3 — 2026-10-15

From a field test of 0.15.2 on three sample projects: the install was right,
`pythia doctor`'s verdicts were not.

- **doctor no longer calls a covered shared hook a failure.** After an
  install from a venv, the team's `.claude/settings.json` still ran
  `python -m pythia`, which cannot start on that machine, while
  `.claude/settings.local.json` served every one of those hooks. doctor said
  `fail`, "the guard is off", counted it among the problems that "stop
  approvals or the guard", and advised re-running install — which, by
  design, never changes a shared file. Now a shared hook that cannot start
  is a warning when this machine's own files (`settings.local.json`, or
  `~/.claude/settings.json`) already serve every hook it would run: it names
  them, says the shared copy will show an error on each call it matches, and
  leaves keep-or-remove to the developer. Otherwise the row names exactly
  which jobs no launcher that starts here still does — the sqlplus guard,
  the AskUserQuestion approval, the session guide, the verdict reminders —
  and is a `fail` only when a gate (the first two) is among them, with the
  fix that works for its file: re-run install, which adds this machine's
  launcher to `settings.local.json`.
- **Codex's session hook is a warning with its real scope.** It carries only
  the session guide and live state — no guard — and AGENTS.md carries the
  rules; doctor says so, and that `.codex/` stays portable.
- Each `fail` names what it leaves off and the fix for its own file: the MCP
  approver ("the pythia_approve door stays closed", fixed through Claude
  Code's local scope), a global hook (`pythia install -g`).

## 0.15.2 — 2026-10-14

Fixes from a field test of 0.15.1 in a sample project, with Claude Code
itself started where pythia's venv was not active.

- **A hook that could not start pythia turned the guard off without a
  word.** The hooks and the MCP approver ran `python -m pythia`; install only
  checked that some `python` existed. Installed in a venv or with pipx, with
  Claude Code started from a desktop app or IDE, that `python` was the system
  interpreter: "No module named pythia", which Claude Code treats as a
  non-blocking hook error — the PreToolUse guard let a sqlplus command
  through, the MCP approver never connected, and `pythia doctor` said ok.
  - `pythia install` now picks the launcher by what really starts pythia: a
    bare `python -m pythia` (or `python3`) only when it imports pythia outside
    a virtual environment (venv, pipx, uv, or any conda environment);
    otherwise the absolute interpreter (or, for a clone, the interpreter plus
    `scripts/pythia.py`). A bare name is resolved on PATH alone and that path
    is what runs — Windows would otherwise try the interpreter's own
    directory and the current directory first.
  - A machine's own launcher goes only into that machine's files: the hooks
    and their deny rule into `.claude/settings.local.json` (added to
    `.git/info/exclude`, found through `.git` files in worktrees and
    submodules), the MCP approver into Claude Code's local scope via
    `claude mcp add --scope local` (printed to run by hand when the `claude`
    CLI is not on PATH; a failed update puts the old entry back). A
    `settings.local.json` that git tracks is the team's file: install does not
    write a machine path into it and says how to untrack it. The shared `.claude/settings.json` and `.mcp.json`
    never get a path from one machine; pythia hooks already in the shared
    file are named, since they run too. A moved venv is respelled in the
    machine's files on the next install — only commands a pythia launcher
    starts; another tool's `hook post-tool` is left alone. `-g` writes the chosen launcher into
    `~/.claude/settings.json`, which is per-user already. Codex's `.codex/`
    files stay portable and install says what to put in `~/.codex/config.toml`.
  - `pythia doctor` starts every launcher it finds — project and local
    settings, `~/.claude/settings.json`, `.mcp.json`, the local MCP scope,
    `.codex/hooks.json` — and fails one that cannot import pythia; it warns
    when a bare `python` only finds pythia inside the active venv or conda
    environment. It runs only an interpreter plus `-m pythia` or an absolute
    `pythia.py` path — never a relative path — with no shell, from an empty
    directory, and from a shared (or git-tracked) file only `<bare> -m
    pythia`: any path there is the repository's and is named, not run.
  - The pythia block in `CLAUDE.md` tells the agent what a session with no
    `STATE` line or no `pythia_approve` tool means: the hooks are not running
    — say so before any write and ask for `pythia doctor`.
- **`.pythia/connections.json` is created owner-only (0600).** It was
  created 0644 — readable by every user on the machine once it holds a
  password — and pythia then warned about its own file on every command.
  The warning for an existing readable file is printed once per run.
- **"Added the pythia block to CLAUDE.md"** when the block joins a file of
  yours; "Updated" now means a block was replaced (AGENTS.md too).
- **`pythia lint` counts comments across the files it lints together.** A
  release keeps its `COMMENT ON` statements in their own file; linting the
  set no longer reports those tables as uncommented.

## 0.15.1 — 2026-10-14

- **`pythia install` writes the harness into `CLAUDE.md`.** Claude Code
  loads CLAUDE.md every session; until now it got pythia's rules only from
  the SessionStart hook, which does nothing with `--no-hooks`, before a
  restart, or while the hooks are untrusted. A marker-delimited block —
  the same brief guide as AGENTS.md, plus Claude's approval doors in order
  and the standing rules agents break most — is merged in, the developer's
  own text untouched, a re-run replacing it in place. With the block present
  the SessionStart hook prints only the live state instead of the guide a
  second time; `pythia doctor` reports whether it is there. Project scope
  only: a global CLAUDE.md would put pythia into every session.
- The repository has a `CLAUDE.md` of its own for sessions working on pythia
  itself: it imports AGENTS.md and adds the contributor checklist.

## 0.15.0 — 2026-10-14

Four requests from the field, one release: approval that works in every
permission mode, a way to let the agent work without approving every write,
an Oracle build pack (tables, comments, packages, tuning, releases) held to
the LEAD's standards, and a harness that catches the agent's mistakes
earlier.

- **Approval no longer depends on `AskUserQuestion` rendering.** Reported:
  the approval question never appeared outside `bypassPermissions`, so no
  answer arrived and nothing could be approved. Claude Code documents
  AskUserQuestion as always prompting, but a known class of setups makes it
  return empty without showing anything — an allow rule or a skill's
  `allowed-tools` naming it, a PreToolUse hook whose matcher is empty or
  `*` (anthropics/claude-code#13439, #29547). Three fixes:
  - **The MCP approver now serves Claude Code too.** `pythia install`
    registers the write-free `pythia mcp` server in `.mcp.json` (and enables
    it); the plugin declares it. Its `pythia_approve` tool is marked
    `anthropic/requiresUserInteraction`, so Claude Code prompts for it on
    every call in every permission mode except `dontAsk`, no allow rule can
    skip it, and its elicitation shows pythia's own card. It is now the first
    door everywhere; AskUserQuestion is the fallback.
  - **The chat hook fails closed and says why.** It refuses answers that
    arrived pre-filled in `tool_input.answers` (written by the agent or a
    hook, not chosen by a person), and when a pythia question comes back with
    no answer it tells the agent the question never rendered, to switch to
    the tool, and to run `pythia doctor` — instead of staying silent while
    the agent retried the same broken door.
  - **`pythia doctor`** checks the wiring and names every setting of that
    class it finds, in the project's and the user's settings, skills and
    installed plugins. `--db` adds the database environment.
- **Autopilot: delegate a session once.** `pythia autopilot request` prints a
  card naming the connection, the statement groups, the hours and a write
  budget; the developer approves it through any door (`autopilot on` at a
  console does both). While the lease lasts, `apply` and `deploy` land
  covered statements without a per-write approval — snapshot, journal and
  verify unchanged. A lease never covers a connection marked `protected` /
  `env: prod`, destructive statements unless the card included them by name,
  or a file with lint errors; it pauses itself when objects that were VALID
  break or after five failures in a row. `autopilot report` lists every write
  with its verdict and undo; `autopilot off` revokes. A lease overrides a
  `deny` policy only for the groups its card names, and the card says so.
- **`pythia deploy`: release scripts through the write path.** Splits
  SQL*Plus-style scripts (`;`, lone `/`, literals and q-quotes respected,
  directives skipped, `@includes` and anonymous blocks refused), orders a
  directory by what each file creates unless it is numbered, refuses policy
  violations before anything runs, previews every statement, takes one
  approval for the set (a 10-hex token), and runs each through apply's six
  steps — stopping at the first refusal or exit 3 with the undo list.
- **`COMMENT ON TABLE / COLUMN` is the `comments` group** (default
  `confirm`) with a real undo: the previous comment is snapshotted and
  `journal restore` puts it back. Before this, comments were refused as
  unclassifiable. `pythia comments T` shows what is documented;
  `--scaffold` prints the statements for what is not.
- **`pythia lint`: the standards before the apply.** PL/SQL antipatterns,
  DDL standards (primary keys, named constraints, CHAR semantics for
  Vietnamese text, `ADD NOT NULL` without default), and script-wide rules
  (every new column commented, every FK indexed). The LEAD tunes severities,
  mandatory comments, required columns, the parameter prefix and forbidden
  patterns in `conventions.json` under `"lint"`. Every apply preview shows
  the findings; exit 2 on errors.
- **`pythia explain` and `pythia stats`** — the estimated plan (EXPLAIN PLAN
  + DBMS_XPLAN, parsed never run, rolled back) with notes on full scans,
  Cartesian joins, implicit conversions and dynamic sampling; a table's
  statistics, indexes and unindexed foreign keys. License-safe.
- **Hooks that enforce the harness.** PreToolUse on Bash and SQLcl
  `run-sql`: pythia's read-only commands (one plain invocation, nothing
  chained) and already-approved writes pass without a prompt; sqlplus/SQLcl
  DDL and DML are denied with the reason; a forged `approve --hook` is
  denied. PostToolUse on Bash puts the exit-code verdict next to an apply
  result. SessionStart now prints the guide plus the live state
  (connections, lease, conventions, approval door); the old `guide --brief`
  hook is upgraded in place by `install`. No PreToolUse matcher is ever `""`
  or `*`. Opt-outs: `auto_allow_reads`, `block_raw_writes` in
  `.pythia/settings.json`. Hook commands use `python3` where `python` does
  not exist.
- **Six new skills** — `pythia-table` (DDL standards, comments, expand and
  contract), `pythia-package` (spec as contract, exceptions, transactions,
  thin triggers), `pythia-tune` (measure, change one thing, measure again),
  `pythia-deploy`, `pythia-autopilot`, `pythia-verify` (evidence before
  "done") — with references for DDL standards, PL/SQL standards, a tuning
  checklist and a robust environment setup. The router, `pythia-apply`,
  `-write`, `-review`, `-conventions`, `-setup` and `-skill-author` know
  them. `pythia skill new` / `skill lint` let a team grow its own skills to
  the pack's contract.
- **Hardened after an adversarial review of this release, before it
  shipped.** The PreToolUse hook no longer trusts shlex's idea of a comment
  (`pythia guide#;touch x` had read as one read command), classifies with the
  CLI's own parser, and allows only the real pythia executable; the CLI
  accepts no abbreviated flags. A deploy card shows every statement in full
  with its row set; member approvals are bound to their deploy and withdrawn
  when it stops; every statement is checked before the first runs; `ALTER
  SESSION`, `EXEC`, `HOST` and `CONNECT` are refused in releases; numbered
  directories sort naturally (and `V3__` counts as numbered); `--from N`
  resumes. INVALID dependents are checked after every write, not only
  PL/SQL. Security-relevant grants and always-true WHERE clauses are
  destructive by code, not by an editable lint rule; autopilot DML above
  1,000 rows goes to a person; protected is re-read on every write. Tokens
  are validated before they name a file; the lease card no longer embeds the
  invocation (the chat door had rejected a real Approve); quoted identifiers
  can no longer escape the journal; `WITH FUNCTION` queries are not
  read-only.
- `prepare_statement` no longer reads a `;` inside a literal as a second
  statement (`set note = 'a; b'` was refused).
- `pythia.__version__` exists; the MCP server reports it.

## 0.14.2 — 2026-09-14

- **The Codex agent now knows to use the `pythia_approve` tool.** With 0.14.1
  the mechanism worked, but a field test showed the agent running `pythia
  apply`, printing the token, and stopping: the preview's follow-up and the
  `pythia-apply` skill only named `AskUserQuestion` (Claude's tool, which Codex
  does not have) and the console — never the `pythia_approve` MCP tool that is
  Codex's actual door. Three places now route it there: the apply preview
  prints an "on Codex: the agent calls the `pythia_approve` MCP tool with token
  …" line; the AGENTS.md harness block (loaded every Codex session) says the
  approval door IS that tool and that the agent then runs `apply --confirm`
  itself, rather than stopping at the preview or handing the developer a
  command to type; and `pythia-apply` step 3 says the same. Confirmed on live
  Codex v0.154: after the preview the agent calls the tool and the developer
  gets the Approve prompt.

## 0.14.1 — 2026-09-14

- **install no longer breaks Codex's config.** 0.14.0 wrote an
  `[approval_policy.granular]` table carrying only `mcp_elicitations = true`,
  but that table requires several fields on Codex v0.154 (`sandbox_approval`,
  `rules`, …) and an incomplete one fails at config-load — the developer's
  Codex would not start (`missing field sandbox_approval`). Seen the first
  time 0.14.0 was installed against a real Codex. install now writes **only**
  `[mcp_servers.pythia]`; MCP elicitations surface under the default
  interactive approval policy without any granular table, and the approval
  policy is the developer's to set, not pythia's. The rest of 0.14.0 is
  confirmed working against live Codex v0.154: the server connects, the
  `pythia_approve` elicitation renders its card with Approve/Reject, and
  Approve mints a grant (`approver="mcp"`).
- **requirements.toml is no longer written.** The danger-full-access pin
  became a printed warning instead. That file is enterprise-managed config and
  its schema is version-fragile in exactly the way that just broke
  `config.toml`; the MCP approver is fail-closed regardless, and the
  elicitation asks for an explicit Approve.
- The `pythia_approve` result now tells the agent to run `apply --confirm`
  itself on the file it previewed, rather than reading like a command for the
  developer to type by hand.

## 0.14.0 — 2026-09-14

- **The Claude approve-and-execute experience, now on Codex.** 0.13.0 put the
  harness on Codex but left approval on a second terminal. The developer wanted
  what Claude gives: the agent asks, a select prompt appears, they click
  Approve, the write runs — inline. The blocker was that Codex's hooks cannot
  read a human's answer and it has no custom question tool (openai/codex#28833,
  #23465). The one mechanism that carries a verified human choice back is MCP
  elicitation (Codex >= v0.120): a new `pythia mcp` server, write-free, exposes
  one tool, `pythia_approve`. It shows pythia's own card as a select prompt, and
  the developer's Approve mints the same grant the console mints — single use,
  connection-bound, `approver="mcp"`. Writes still never go through MCP; `apply`
  still verifies the grant. Everything but an explicit accept-with-Approve is
  fail-closed, so a headless run or an older Codex simply falls back to the
  console `pythia approve <token>` — nothing regresses.
- **The one hole is pinned shut.** Codex auto-approves elicitations under
  `danger-full-access`, so `pythia install` writes a `.codex/requirements.toml`
  the agent cannot override, disallowing that sandbox and `never`-approval —
  and it registers the server in `.codex/config.toml`. Both edits are
  append-only and refuse to touch a file that already defines those tables,
  printing what to add instead: the stdlib cannot safely merge TOML, and a
  developer's config is not ours to corrupt.
- Approval on Codex is otherwise unchanged in substance from Claude: the card is
  pythia's own, built from the journal, so there is nothing for the agent to
  paraphrase. Proven end to end by driving the real `python -m pythia mcp` over
  OS pipes — initialize, tools/list, a pythia_approve call, an elicitation
  answered Approve, a grant minted `approver="mcp"`.

## 0.13.0 — 2026-09-14

- **`pythia install` now serves Codex, not only Claude Code.** Until now Codex
  got one thing — `npx skills add` reaching it as one of ~76 agents — and
  nothing else: the harness never loaded, the no-Node fallback wrote a
  directory Codex does not read, and no page told a Codex user where the
  approval door was. Three verified facts about Codex shape the fix. Its
  always-on instruction file is `AGENTS.md` (loaded every session), so
  `install` merges a marker-delimited pythia block into `<project>/AGENTS.md` —
  the same `BRIEF_GUIDE` the Claude session-start hook runs, one source so the
  two cannot drift, and the developer's own AGENTS.md text is left untouched.
  Its skills load from `.agents/skills/`, not `~/.codex/skills`, so the no-Node
  fallback now populates that too (Node users were already served). And its
  hooks live in `<project>/.codex/hooks.json`, so a session-start guide hook is
  wired there, behind the same `--no-hooks` flag and a one-line `/hooks` trust
  reminder.
- **The guide stays per-project on Codex, as it does on Claude.** A global
  `~/.codex/AGENTS.md` would load the harness into every session on the
  machine, Oracle or not — the same noise the Claude design keeps out of
  `~/.claude/settings.json`. So `-g` gives Codex only the skills.
- **The chat approval gate does not port, and the docs say so plainly.** Codex
  has no question tool and its PostToolUse cannot read a user's answer, which is
  exactly what makes Claude's mint work. On Codex the developer approves in the
  terminal — `pythia approve <token>` — and the AGENTS.md block and
  `pythia-apply` both say it. `apply` enforces the grant either way, so the
  write gate is unchanged. A defensive Codex `PermissionRequest` hook is parked
  in IDEAS.md, pending a live-Codex field test.

## 0.12.0 — 2026-09-11

Five findings from one session on an editions-enabled schema, 10-11/09. Two
of the four bugs reported were already half-fixed by 0.11.1 and 0.11.2; the
halves that were left are here, and the one whose cause turned out to be
something else entirely got the fix the evidence asked for instead of the
one that was requested.

- **A refused write still read like one that landed.** 0.11.2 put the driver
  error back in its place by line-buffering both streams, but the last word
  was `Oracle error: ORA-38824` from a top-level handler, printed after the
  preview, the diff and `Snapshot saved` had all gone out. Read through
  `| tail -20` — which is how an agent reads it — the run was
  indistinguishable from a success. `cur.execute` is guarded now, and apply
  says `FAILED — nothing was written`, plus the thing the reader needs next:
  the approval was not spent, because it never got as far as spending it.
- **`apply` asks the database which editionable keyword the object has.**
  0.11.1 taught the classifier, the journal header and the diff to carry
  NONEDITIONABLE, and all three read it off the file. Nobody asked the
  database, so the loop stayed open: `ALL_SOURCE` stores no CREATE header,
  `src` hands back a bare `PROCEDURE "NAME"`, the statement built from it
  says `CREATE OR REPLACE PROCEDURE`, and Oracle answers ORA-38824 — after
  the developer has approved a preview that could never have worked.
  `name-occupants.sql` was already asking `ALL_OBJECTS` about that owner and
  name, so it returns `EDITIONABLE` too, and the statement is reconciled to
  it once, before the snapshot. Announced in the preview, never silent; the
  file on disk is untouched; the confirm token still hashes the file, so
  reconciling does not move it. The journal records what ran rather than
  what the file said, or `restore.sql` would rebuild its header without the
  keyword and put the 0.11.1 bug back in the undo.
- **The preview says when the object moved outside pythia.** Reported as
  *`src` returned a line the database does not have*. The journal disagrees,
  and the journal is a recording: an apply wrote 118 lines, the preview two
  seconds later read 118 back with the line intact, and eight minutes on the
  next preview read 117 without it — no entry in between. `src` was honest at
  both readings; something that was not pythia did the writing. Every apply
  already stores what it wrote, so the preview now compares that against
  `ALL_SOURCE` as it stands and reports the line delta and the entry to look
  at. A warning, not a refusal: editing by hand between two runs is
  legitimate, and not noticing is the only part that hurts. Replayed over the
  41 journal entries that produced the report it fires three times, all three
  after the line vanished, and stays quiet on the read two seconds after the
  write. IDEAS.md parked this as *cooperative object lock* on the strength of
  an argument; it has a recording now.
- **The approval question is the card, and only the card.** *"An
  `AskUserQuestion` whose text is that card verbatim"* read, to at least one
  agent, as *contains* that card verbatim. It wrapped the card in a sentence
  of its own; the hook refused the paraphrase, correctly — and the refusal
  arrived after the developer had already clicked Approve, because
  `PostToolUse` is the only moment a hook gets. The wasted click is the whole
  cost of the ambiguity, so `pythia-apply` now forbids the specific thing
  that happened, and says where the explanation goes instead: the option
  descriptions, which nothing compares against.
- **`pythia-comment` is the eleventh skill.** One line,
  `date - author - what changed`, ASCII, and a table of where the second line
  belongs instead. It earned the slot in the same session: a five-line
  explanatory block above a single select-list expression, one of whose lines
  was then lost to a second writer — and that loss is what sent the developer
  hunting a bug in `src`. Scoped to PL/SQL and `.sql` deliverables; the draft
  also covered C#, JS and Razor, but this pack installs into every project a
  developer has.

## 0.11.2 — 2026-09-11

- **Chat approval was impossible for any card containing a non-ASCII
  character.** `main()` reconfigured stdout and stderr to UTF-8 but left
  stdin alone, so on Windows the `AskUserQuestion` hook payload — UTF-8 on a
  pipe — was decoded as cp1252 and the em dash in *"There is no snapshot for
  this group — after commit it cannot be undone"* arrived as `â€"`. The card
  built from the journal still held U+2014, the substring check missed, and
  the hook answered *"the question did not carry pythia's approval card
  verbatim"* on a question that carried it exactly. Every structural
  statement was affected, forever: no amount of re-asking could match.
  Reported live on CORE_BH, where three approvals in a row were refused while
  four plain-ASCII cards on the same object went through. stdin now joins the
  other two streams. The whitespace-insensitive comparison was never the
  problem, and is unchanged.
- Console output no longer prints out of order when both streams are pipes.
  stdout was block-buffered while stderr was not, so a driver error — an
  ORA-38824 from `apply --confirm` — surfaced *above* the preview it belonged
  to, with `Snapshot saved` as the last line, and a failed apply read like a
  successful preview. The streams are line-buffered now, so the conclusion is
  wherever it actually happened: last.
- `apply --confirm` with a token from a preview that already ran says so,
  instead of blaming the file. *"The file or the database object changed
  since that preview"* is a different bug with a different fix, and it sent
  agents hunting a difference that was not there.

## 0.11.1 — 2026-09-05

- **NONEDITIONABLE objects could not go through `apply` at all.** The
  classifier spelled Oracle's keyword `NOEDITIONABLE`, without the middle N,
  so `CREATE OR REPLACE NONEDITIONABLE PROCEDURE ...` never matched
  `plsql_source` and fell through to `structural` — which is deny by default
  and counts statements by semicolon, so a PL/SQL body came back as *"The
  file contains more than one statement"*. The only way past it was deleting
  the keyword, which Oracle then met with ORA-38824 on an editions-enabled
  schema. Reported live on CORE_BH (`PBH_FILE_NH`,
  `PBH_BT_TRINH_DUYET_HUY`), where it meant every such object was applied by
  hand — no snapshot, no impact, no journal. Both spellings now match; only
  the database decides whether the statement is valid.
- **And the undo those objects would have got was broken the same way.**
  `ALL_SOURCE` stores no `CREATE` header, so the journal builds one — as a
  bare `CREATE OR REPLACE`, which on an editions-enabled schema is ORA-38824
  against a NONEDITIONABLE object: a rollback that fails exactly when it is
  needed. The header now carries the keyword from the file being applied,
  which is the property the database holds — Oracle refuses to *change* it
  through `CREATE OR REPLACE`, so a file that applies cleanly already spells
  it right.
- The preview's diff builds the same header, so a NONEDITIONABLE file no
  longer shows its own first line as a change that is not one (a real
  two-line edit read as "4 lines changed"). The confirm token is unaffected:
  it hashes the file and `ALL_SOURCE` as they are, not the rendered header.

## 0.11.0 — 2026-09-03

- **`pythia install` wires the Claude Code hooks.** 0.10.0 shipped the chat
  approval door, but a pip or npm install left it closed: the `PostToolUse`
  hook lived only in the plugin and in an example file, and the first field
  test of 0.10.0 ended with a developer pasting JSON by hand. `install` now
  merges both hooks —
  SessionStart guide, PostToolUse approve — and the `deny` rule on
  `Bash(pythia approve --hook*)` into `.claude/settings.json`. Merged, not
  replaced: every other key survives, a hook already present in another
  spelling (`python3`, the plugin's path) is recognised, a second run adds
  nothing, and a file that is not valid JSON is left byte-identical with a
  note. `--no-hooks` skips the step.
- `install -g` writes only the approve hook and the deny rule into
  `~/.claude/settings.json`: that file applies to every project on the
  machine, and the session guide would be noise outside Oracle work.
- The allow list and `autoMode` guidance stay in the example file: those are
  the developer's permission preferences, not pythia's to set.
- After `pip install --upgrade pythia-plsql`, run `python -m pythia install`
  once more — pip runs nothing after an install. `npx pythia-plsql` already
  does both.

## 0.10.0 — 2026-09-02

- **The developer approves in chat.** Approving used to mean leaving the
  conversation, opening a terminal, and typing `pythia approve <token>` once
  per preview — four times for the four-file change that prompted this
  release. Now the agent asks: `pythia approve --card <token>` prints the
  approval card (object, statement or impact, connection, time — the same
  words the console command shows, read from the journal and never
  recomputed), the agent puts it verbatim into an `AskUserQuestion` with the
  options **Approve** / **Reject**, and the developer clicks. A `PostToolUse`
  hook on `AskUserQuestion` runs `pythia approve --hook`, which reads the
  payload Claude Code wrote and mints the same single-use, 15-minute,
  connection-bound grant the console mints.
- **The hook refuses a paraphrase.** It mints only when the answer is exactly
  `Approve` *and* the question carried pythia's card verbatim (whitespace
  aside). An agent that summarised the change in its own words gets no grant
  and a note saying to ask again with the card — the developer approves
  pythia's preview, never the agent's description of it. `Reject`, free text,
  another tool, garbage on stdin: nothing minted, and nothing said unless a
  token was involved.
- **What the agent learns.** The hook's `additionalContext` tells it what
  happened per token — minted (and the exact `--confirm` line), rejected, or
  refused and why — so the next move is never a guess.
- **Console approve takes several tokens at once.** `pythia approve a1 b2 c3`
  mints three grants in one act; the card is printed for each.
- The grant records `approver` (`console` or `chat`) and the chat `session`
  id — audit data, like `revalidate`.
- The plugin ships `hooks/hooks.json` with both hooks (SessionStart guide,
  PostToolUse approve). The example settings carry the same, plus a `deny`
  on `Bash(pythia approve --hook*)`: the hook is meant to run on a payload
  the client wrote, and an agent piping a hand-made one would be forging.
  SECURITY.md says so in its layer table.
- Preview follow-up lines now name both doors — the card for chat, the
  command for a terminal — and the `no developer approval` refusal does too.
- The console door is unchanged: still a real console only, still no
  `PYTHIA_CI` escape. `--card` needs no console and mints nothing.

## 0.9.0 — 2026-08-28

- **A `data_dml` write is now approved on the rows it touches.** The preview of
  an `UPDATE` or `DELETE` prints how many rows the statement affects and up to
  ten of them; `pythia approve` shows the same rows again; and
  `apply --confirm` refuses if that set moved in between. The mechanism reuses
  the machinery that already existed rather than adding a gate: the row-set
  fingerprint (count plus a ROWID hash) goes into the confirm token's payload,
  where the object's source goes for `plsql_source`. A moved set therefore
  produces a different token, and the refusal names both numbers — "12 rows
  when you approved it, 15 now".
- **Bug, and the reason the release exists at all: `data_dml` never
  committed.** There was no `commit()` anywhere in the tool. DDL groups commit
  themselves, which hid it, and the process ended by force-closing the pool —
  so an `INSERT`/`UPDATE`/`DELETE` was rolled back *after* the report printed
  "Applied". It commits now, and only after `cur.rowcount` is compared with
  the approved count: a divergence rolls back and exits 1, which also closes
  the millisecond window between the check and the write.
- **Refused rather than guessed at.** `MERGE` is refused (its rows come from a
  join, so no honest preview of them exists), as is any `UPDATE`/`DELETE` whose
  row set cannot be derived without guessing — a subquery, a second `WHERE`, an
  alternative-quoted literal. `INSERT` applies with no revalidation and the
  preview says why: nothing exists beforehand to show. A probe that errors is a
  refusal too; failing to measure is never a reason to proceed.
- **`data_dml` stays `deny` by default.** Turning it on remains a deliberate
  act. What changed is the refusal text: it now says what revalidation buys and
  what it cannot — nothing here undoes a committed `DELETE`.
- `journal restore` on an entry from a group with no snapshot (`data_dml`,
  `structural`, `grants`) now refuses with what is actually true. It used to
  generate `DROP DATA_DML STATEMENT`, which failed as a *structural* policy
  denial — a confusing answer to a reasonable question.
- The apply report no longer offers a restore command for those groups, and
  says plainly that the journal kept the statement, not a way back.
- The grant's reserved `revalidate` field now carries the fingerprint the human
  was shown. It is audit data, not a second gate — the token is the
  enforcement.

## 0.8.0 — 2026-08-27

- **Breaking, and the point of the release: a write now needs a human's
  approval, not just a matching token.** `pythia apply <file> --confirm
  <token>` additionally requires a grant that only `pythia approve <token>`
  can mint — and approve runs only at a real console, with no `PYTHIA_CI`
  escape. The token proved the *content* had not moved; it never proved
  anyone had *approved*. Skills are text: they tell an agent to stop and ask,
  and nothing stopped one that did not. Now something does.
- **The preview prints two follow-up lines instead of one** — the developer's
  `pythia approve <token>` and the agent's `apply --confirm`. Relay both.
- Grants are **single-use**, expire after **15 minutes**, and are bound to the
  connection the preview ran on: approving on `dev` does not approve
  `staging`. Expired ones are swept on the next approve; there is no prune
  command to remember.
- `approve` shows what is being approved — object, type, schema, impact,
  the connection and time of the preview — read from the preview's journal
  entry, never recomputed. A bare hash cannot be approved blind, and for the
  groups with no snapshot it shows the statement itself and says plainly that
  nothing can undo it after commit. It touches no database, so a developer's
  terminal with no connection configured can still approve.
- `journal restore` goes through the same gate, because it goes through the
  same write path. One door for writes is now a provable claim.
- The journal records `confirmed_via: "grant"` and when the grant was minted,
  so approve-to-apply latency is audit data.
- **Agents' command line is unchanged**: `apply --confirm <token>`, as before.
  What changed is that it refuses until a person has acted.
- `--yes` at a real terminal is untouched — a human previewing and applying in
  one motion *is* the approval act. Headless `--yes` stays refused.
- Honest limit, stated in SECURITY.md rather than papered over: the gate
  assumes a developer with a terminal on the machine holding the repo. A
  purely remote developer has no clean path yet, and deliberate forgery of the
  grant file remains possible — the same tier as editing `policy.json`. What
  the grant raises is the bar, not a wall.

## 0.7.1 — 2026-08-27

- **The three-role layout is now documented as the named pattern** (GUIDE
  section 3, both languages, and the example SQL): ADMIN for administration
  only, OWNER holding the schema and *never* DBA — because a proxy session
  inherits the owner's entire power — and AGENT with logon only, proxying
  into the owner. Straight from a field restructure where the missing third
  role was the lesson: after stripping DBA off the owner, admin work needs
  somewhere unreachable by the agent to live. Includes the connections.json
  shape (agent entry as default, direct-owner entries that make `check` warn
  by design) and the standing answer to "why not ANY grants" — on a shared
  instance, one wrong run with CREATE ANY PROCEDURE touches someone else's
  code.
- `examples/agent-user-setup.example.sql` upgraded from two roles to three;
  `pythia-setup` names the trio and points at both.
- Framed explicitly as **advisory**: the layout is the author's guidance —
  one extra hardening layer at the role tier. Sites own their security
  model; the kit works with whatever accounts it is pointed at and takes
  no responsibility for a site's privilege design.

## 0.7.0 — 2026-08-27

**The asking release.** A field session built a complete forgot-password flow
and silently settled seven spec decisions — expiry times, channels, audit
trade-offs — that belonged to the developer. Challenged, the agent's own
diagnosis was exact: the construction chain starts after the spec is settled,
and nothing in the kit owned the moment before. Now two things do, and a
third makes them fire reliably.

- **New skill `pythia-spec`** owns the moment a request arrives with open
  decisions. Its Iron Law: spec decisions belong to the developer — you
  propose, they choose. The line that decides who decides: if two senior
  developers could reasonably ship different behaviour, it is spec (ask);
  if the codebase or conventions already answer it, it is technical (decide,
  and cite the căn cứ). The questions are mandatory; the written spec/plan
  documents are offered afterwards and the developer may skip them. A new
  decision discovered mid-build stops the build.
- **New skill `using-pythia`** — the pocket handbook, after Superpowers'
  router pattern (credited, rebuilt from scratch): route by what the
  developer *said*, in any language, before any action. No slash command is
  ever needed — descriptions now carry the natural phrases, Vietnamese with
  or without diacritics included.
- **`pythia guide --brief`** prints the one-page harness, and the Claude
  Code settings example gains a `SessionStart` hook that injects it every
  session. That hook is what turns "some sessions ask, some don't" into
  deterministic routing — ~15 lines of context, once.
- **Conformance group F** scores exactly this: natural prompts, no skill
  names — does the agent ask before building (F1), explore instead of
  asking back (F2), and route diacritic-free Vietnamese (F3)?
- AGENTS.md rule 11; the guide's Ask movement opens with the request gate;
  `pythia-write` requires a settled spec.

## 0.6.1 — 2026-08-27

- **`pythia connections`** — the configured connections with everything
  except the secrets: name, user (proxy form included), schema, target, and
  which one is the default. Found the hard way: a Claude Code permission
  classifier blocked an agent for reading `connections.json`, and it was
  right to. The agent only wanted the connection names — but the kit gave it
  no sanctioned way to ask, so it reached for the credentials file. Now there
  is one, and a test pins the safety property: fields are copied by name, so
  a key added to the config later cannot leak through, and a configured
  password appears nowhere in either output mode.
- The rule is taught where the temptation is: `pythia guide` puts it in the
  Learn movement, AGENTS.md carries it as a standing rule, and the
  `pythia-setup` file table says it on the `connections.json` row itself.

## 0.6.0 — 2026-08-27

**The kit now states its own operating model — Learn, Ask, Do (Học – Hỏi –
Làm) — and wires it through everything, not just the docs.** The owner's
framing: a harness an agent studies and follows like a book, the way a good
teaching assistant works — knowledge, judgment, and rules it can recite.

- **`pythia guide`** prints the whole model from the tool itself: the three
  movements, every command's place in them, the ask-gates verbatim. No
  database needed. On platforms with no skill support, that page *is* the
  contract — the harness no longer depends on a skills-capable agent.
- **Every skill declares its movement** in a `**Phase:**` line (explore is
  Learn; apply is Ask → Do; conventions is Learn → Ask; ...). The skill lint
  enforces the declaration.
- **Coherence is tested**: every CLI command must have a place in the guide
  — adding a command without deciding where it lives in Learn/Ask/Do fails
  CI. The model cannot silently rot.
- **Docs rebuilt around the model.** Both READMEs open with Learn → Ask → Do
  as the core (the 200-line cap is retired by the owner's call — pinned
  content checks remain); both GUIDEs gain "read this first"; AGENTS.md maps
  its standing rules onto the three movements.

## 0.5.0 — 2026-08-27

**Adopting a house style is now one flow the agent runs, not a form you fill
in.** Hand it a standards document, or point it at the base schema your team
already runs, and it derives the conventions, proves them against the real
names, and writes both halves of the config.

- **`pythia conventions --scan`** reads the patterns off the live schema. It
  tokenises every object name and proposes a regex per type — dominant
  prefixes and suffixes become alternations, and a position with no repeating
  token is left open rather than invented. The agent never pages thousands of
  names into its context to work this out.
- **`pythia conventions --check`** measures the configured patterns against
  the schema: coverage per type, and the names that miss. Below 90% it says
  the pattern is probably wrong rather than the schema — which is almost
  always true of a pattern derived from a document.
- **`pythia conventions --init`** still writes a blank pair for starting from
  scratch. Listing and `--init` need no database; `--scan` and `--check` do.
- **New skill `pythia-conventions`** owns the flow: scan, read the
  developer's document, reconcile and *ask* where the two disagree, write,
  verify, then record real exceptions with the reason they are exceptions.
  Its Iron Law is that no pattern gets written down before the schema has
  agreed with it.

On the schema this was built against, `--check` independently found the two
exceptions a developer had documented by hand.

## 0.4.10 — 2026-08-27

- **`pythia conventions --init` writes the starter pair.** Capturing a house
  style was documented as "copy `examples/conventions.example.json`" — which
  is no help to anyone who installed the wheel, because there is no examples
  directory there. The templates now ship inside the tool: a `conventions.json`
  whose patterns are valid as written, and a `conventions.md` skeleton with
  the sections that matter. Neither file is ever overwritten.
- **The kit now says which of its files come from where.** `pythia-setup`
  gained a table of everything that can live in `.pythia/` and the command
  that creates each — only `connections.json` comes from `install`, and an
  empty `.pythia/` is a working one. `pythia-write` offers `--init` when a
  developer describes a house style with nowhere to put it, which is how a
  rule stops being re-explained every session.

## 0.4.9 — 2026-08-27

- **Connection errors Oracle has already diagnosed now carry their fix.**
  `ORA-28000` used to arrive under generic advice to check host, port and
  credentials — none of which is the problem when the account is simply
  locked. It now names the statement a DBA runs, and the query that says why
  it locked, so it does not lock again. Same for `ORA-28001`/`28002`
  (expired) and `ORA-01017` (wrong password — with the warning that retrying
  is what trips `FAILED_LOGIN_ATTEMPTS` in the first place).
- **The account named is the one that actually authenticates.** Under proxy
  authentication the connect string is `agent[owner]`, and it is the agent
  that locks — not the schema in front of you. Naming the wrong one sends a
  DBA to unlock an account that was never locked.

## 0.4.8 — 2026-08-27

Both fixes come from a field install where everything had in fact worked.

- **"Not on your PATH" now tells a stale terminal apart from an
  unconfigured one.** After `--add-to-path` succeeds, every terminal already
  open still carries the environment it started with, so the command stays
  missing there and the old message sent people to re-run an install that
  had already done its job. When the directory is in the stored PATH but not
  in this process, pythia says so, and says that a new *window* is needed —
  a new tab inherits from the window that spawned it, so Windows Terminal,
  VS Code and Cursor have to be restarted.
- **A PATH near the truncation limit is now flagged.** Windows tooling still
  cuts PATH around 2047 characters, and a freshly appended entry is last in
  line, so it disappears first and silently. The machine this was found on
  sat at 2050, from system entries copied into the user PATH by a
  `$env:PATH` one-liner.

## 0.4.7 — 2026-08-27

- **`pythia install` puts itself on your PATH.** Three field installs in a
  row ended at `pythia : The term 'pythia' is not recognized`, because pip
  writes the executable into a scripts directory that is usually not on PATH.
  0.4.4 explained the problem and 0.4.5 documented a working invocation —
  both handed the developer homework. Install now offers to fix it, and
  `--add-to-path` does it without asking.
- **The PATH edit is done properly, which a one-liner cannot be.** Earlier
  advice in this project — and the shape found all over the internet —
  writes `$env:PATH` back into user scope. That variable is the system and
  user values *merged*, so it copies every system entry into the user's,
  doubling the effective PATH and leaving a stale snapshot that shadows the
  real system one whenever it next changes. pythia reads and writes the user
  value in the registry, appends only if absent, and never touches system
  scope. **If you ran that one-liner, see GUIDE section 1 for how to undo
  it.**

## 0.4.6 — 2026-08-27

- **`SECURITY.md`** — how to report a vulnerability, and the threat model
  stated plainly: what each layer stops, which layers can be walked around
  and by whom, and what a snapshot does **not** restore (rows, a dropped
  column, a revoked grant). Includes the honest reading of the marketplace
  risk score on `pythia-apply`: the substance is fair — it is the skill that
  authorizes writes — while the words that trigger it all sit inside
  prohibitions. Safety rules will not be reworded to score better.
- CI: GitHub Actions bumped to v7, clearing the Node-runtime deprecation
  warning every run was printing; Dependabot now watches them monthly.
- CI: the npm wrapper is executed for real, not just syntax-checked. It is
  the install path most Windows users take.

## 0.4.5 — 2026-08-27

- **The install instructions now use a command that cannot fail.** 0.4.4 made
  `pythia install` explain the PATH problem — but that hint is printed by the
  very command a broken PATH prevents you from running, so the same field
  install failed again in exactly the same place. Both READMEs and both
  guides now show `python -m pythia install`, which is the same program and
  is immune, and explain when the shorter `pythia` becomes available. The
  0.4.4 hint stays: it is still what you see once you reach a working
  invocation.

## 0.4.4 — 2026-08-27

- **`pythia install` now says where the executable went.** A field install on
  Windows ended with `pythia : The term 'pythia' is not recognized` at the
  very step the guide tells you to run next: `pip install --user` — the
  default when writing to the interpreter's own directory needs admin — puts
  `pythia.exe` in a scripts directory that is not on PATH, and the shell's
  error names nothing that helps. Install now checks whether its own command
  resolves, and if not prints the exact directory, the one-line PATH command
  for that platform, and the `python -m pythia` form that works with no PATH
  at all. Both guides carry the same note.

## 0.4.3 — 2026-08-27

- `examples/claude-code-settings.example.json`: optional Claude Code
  permission settings — the 22 read-only pythia commands stop prompting
  (deterministic rules, in the syntax Claude Code itself writes), and the
  auto-mode classifier is asked to pause on `apply --confirm`, `journal
  restore --confirm` and `policy set`. Shipped as an example the developer
  installs, like `agent-user-setup.example.sql`, and NOT installed by
  `pythia install`: it is another product's security config, it covers one
  of 77 supported agents, and its `autoMode` half is advisory — GUIDE
  section 11 says which half is enforcement and which is only a nudge.

## 0.4.2 — 2026-08-27

- **The exit-code contract survives a pipe.** Agents pipe to `tail` to keep
  output small, and `pythia apply … | tail; echo $?` then reports *tail's*
  exit code — always 0. Seen in a real session, where a refusal was
  reported as `EXIT=0`; harmless only because the agent trusted pythia's
  words over `$?`. Read the other way it turns exit 3, written-but-broken,
  into "success". pythia-apply and AGENTS.md now forbid reading `$?`
  through a pipe, a Red Flag row names the mistake, and conformance
  scenario D1b baits it.

## 0.4.1 — 2026-08-27

For a change that never goes through `apply` — a DBA runs it, a release
process owns it, policy denies the group — the rollback file was already
being written, and was impossible to find.

- **The preview names the rollback file.** It has always written
  `restore.sql` holding the version currently live; the preview now prints
  that path and says what it is for.
- **`journal prune` stopped being able to delete a unique rollback.** A
  preview wrote nothing to the database, but its `restore.sql` is the only
  undo for a change the developer then ran by hand. Prune now drops a
  preview only when a newer entry already keeps a byte-identical rollback.
- **pythia-apply** requires handing that path over in the same message as
  the `.sql` file whenever the developer will run it themselves.

## 0.4.0 — 2026-08-27

**The safety net now covers work done by hand.** Until now the journal only
held what went through `pythia apply` — so a developer editing in SQL
Developer, or an agent restricted to read-only exploration, had no snapshot
and no rollback at all, while the docs implied otherwise.

- **Automatic snapshots.** `src` and `impact` capture the object's current
  source into the journal. `impact` is already mandatory before any change,
  so the capture happens exactly when it matters without anyone having to
  remember a command. Content-hashed: reading an unchanged object writes
  nothing.
- **Costs the agent no context.** Capturing is completely silent. The only
  thing ever printed is drift, and it goes to stderr, so `--json` stays
  parseable.
- **Drift detection falls out of the same mechanism.** Source that moved
  with no apply of ours behind it means someone changed it outside pythia:
  `src` and `impact` say so on the spot, and `check` summarises it in one
  line using `LAST_DDL_TIME` (one query, not one per object).
- **`pythia history <OBJECT>`** — every captured version, newest first, with
  the ready-to-run rollback file for each. That is the index for choosing
  what to go back to.
- **Every version carries a rollback file.** `restore.sql` is written for
  snapshots as well as applies — a plain `CREATE OR REPLACE` you can hand a
  DBA when pythia is not in the loop.
- **`journal prune` no longer eats snapshots** — for a hand-edit they are
  the only undo that exists.
- Opt out with `{"auto_snapshot": false}` in `.pythia/settings.json`.

## 0.3.5 — 2026-08-27

- **A skill silently lost its trigger.** The 0.3.3 rewrite put an unquoted
  `": "` in pythia-explore's description; YAML reads that as a nested
  mapping, so the field was lost and the harness fell back to the H1
  heading. The skill kept its name and stopped firing — the exact failure
  0.3.3 set out to fix. Description rephrased, and the skill lint now
  rejects an unquoted `": "` in any frontmatter value (stdlib only, so it
  runs in CI like the rest).

## 0.3.4 — 2026-08-27

- **The bundled-pack copy destroyed a symlinked install.** `npx skills add`
  leaves `.claude/skills/<name>` as a symlink into `.agents/skills/<name>`.
  `copy_bundled_skills` wrote through that symlink and then deleted the
  target as a stale copy, leaving dangling links and no readable pack —
  seen on a real machine after refreshing skills. Both destinations are now
  cleared link-first before copying, and a regression test builds the
  symlink layout and proves the pack survives.

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
