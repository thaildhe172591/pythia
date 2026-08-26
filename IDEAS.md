# Ideas parked for later

Good ideas that are out of scope right now. The rule (from the project spec):
write them down here, do not implement them on impulse.

- **Compile with PL/Scope from inside `apply`.** The write session could run
  `ALTER SESSION SET plscope_settings='IDENTIFIERS:ALL, STATEMENTS:ALL'`
  before applying `plsql_source`, so every object that goes through pythia
  builds the semantic index as a side effect — on a dedicated dev schema the
  index would simply always be complete. Session-scoped, so it affects nothing
  else. Needs a switch (project setting), a test, and a note in the apply
  skill before it ships.
- **Detect proxy sessions in the privilege warning.** With proxy
  authentication (`agent[owner]`) the session user IS the owner, but the
  connection string is not, so the owner warning stays silent today. Honest
  refinement: read `sys_context('userenv','proxy_user')` and phrase the
  warning differently for sanctioned proxy setups.
- **`journal prune`.** Preview-only entries accumulate one directory per
  preview. Harmless, but a `journal prune --previews` would keep the list
  readable once real usage produces hundreds.
