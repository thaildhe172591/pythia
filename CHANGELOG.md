# Changelog

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
