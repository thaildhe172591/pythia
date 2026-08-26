# Ideas parked for later

Good ideas that are out of scope right now. The rule (from the project spec):
write them down here, do not implement them on impulse.

- **Scaffold engine (`pythia new`).** With conventions.json in place, the
  next step writes itself: `pythia new table <NAME>` generating the full
  house-style set — table, sequence, trigger, CRUD procedures — from a
  project-local template directory (a real module the team already wrote,
  used as the mold). The conventions engine warns on drift; the scaffold
  would prevent it. Needs a template format decision — brainstorm first.

Shipped from this list in 0.2.0: PL/Scope compile inside `apply`
(settings.json switch), proxy-aware privilege warnings, `journal prune`.
