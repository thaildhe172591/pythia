# Ideas parked for later

Good ideas that are out of scope right now. The rule (from the project spec):
write them down here, do not implement them on impulse.

- **Scaffold engine (`pythia new`).** With conventions.json in place, the
  next step writes itself: `pythia new module <NAME>` generating the full
  house-style set — table, child tables, sequence, trigger, CRUD procedures
  — from a real module the team already wrote, used as the mold. The
  conventions engine warns on drift; the scaffold would prevent it.

  *Status 2026-08-27: paused, but the blocker is gone.* The template-format
  decision is taken and written up in
  `docs/superpowers/specs/2026-08-27-phase6-scaffold-engine-design.md`
  (local only — `docs/` is gitignored). In short: one JSON template with a
  single `{{STEM}}` placeholder and a suffix per member, no engine, because
  the naming rule already makes every derived name the stem plus a suffix.
  It is fillable two ways — learned off a live base schema, or hand-written
  from an existing conventions analysis when the new environment has no
  route to that database — and `--check-template` verifies either one
  against conventions.json. Generation emits files only; they go through
  `apply` like anything else. The part with no parser answer — telling
  mandatory boilerplate from business logic — is settled once at learn time
  by a human confirming the frame, never guessed at generation time.
  Resume by reviewing the three open questions at the end of that spec.

- **From the 15-repo survey (2026-08-27).** The approval gate shipped as
  0.8.0; these are the rest, in the order they earned:

  - **MCP elicitation as approver #2.** The client renders the approval
    dialog and mints the same grant file; a click instead of a paste, and
    `apply` never learns the difference. *Shipped for Claude Code in 0.10.0*
    as `AskUserQuestion` + a `PostToolUse` hook (`approve --card` /
    `approve --hook`). Still open for other clients: an MCP elicitation
    would mint the same grant the same way, once a write-free MCP surface
    exists to carry it.
  - **Runbook on deny.** A refusal that hands over the exact statement, its
    rollback note, and a copy-paste runbook, instead of stopping at "no".
    `DROP TABLE` → the statement without `PURGE`, plus
    `FLASHBACK TABLE ... TO BEFORE DROP`.
  - **Typed uncertainty in `deps`/`impact`.** Say *why* the graph may be
    incomplete — dynamic SQL, `EXECUTE IMMEDIATE`, synonym chains, missing
    read privileges — instead of returning a falsely clean answer. For a
    tool whose iron law is "no proposal before the blast radius is known", a
    false-clean impact report is the worst failure mode available. Also:
    cross-check `ALL_DEPENDENCIES` against PL/Scope and report the gap.
  - **Cooperative object lock.** Two agents editing one package in a shared
    DEV overwrite each other, and a snapshot does not prevent that.
  - **`explain` and `hotspots`.** License-safe views only: `V$SQL`,
    `V$SQL_PLAN` are fine; `DBA_HIST_*`/AWR/ASH need Diagnostic Pack and stay
    out, as does anything needing Tuning Pack.
  - **A `plsql-safe-write` skill contributed upstream to `oracle/skills`.**
    The strongest distribution channel is the incumbent's own repo.

  Ruled out deliberately, so they stay ruled out: an MCP server for the write
  path (writes never going through MCP *is* the differentiator), a Rust
  rewrite, and a full offline ANTLR parser — that last one contradicts asking
  the live schema, which the drift table at the top of the README exists to
  argue for.

Shipped from this list in 0.2.0: PL/Scope compile inside `apply`
(settings.json switch), proxy-aware privilege warnings, `journal prune`.

Shipped in 0.9.0: row-set revalidation for `data_dml` — approve on the rows,
refuse if the set moved, and the `commit` that group had never had.
