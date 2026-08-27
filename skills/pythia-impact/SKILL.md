---
name: pythia-impact
description: Use before proposing or writing any change to an Oracle object - a procedure, package, function, trigger, view, or table. Impact analysis comes first, because what depends on an object decides how careful the change must be, and Oracle invalidates dependents in cascade.
---

# Impact Before Change

**Announce at start:** "Using pythia-impact to measure the blast radius first."

**Phase:** Learn — and the first Ask gate: a large blast radius reaches the developer before any code is written

Changing an Oracle object recompiles or invalidates everything that depends
on it — immediately, schema-wide, for every user of a shared database. The
cost of knowing first is one command.

## The Iron Law

```
NO CHANGE PROPOSED WITHOUT ITS IMPACT MEASURED FIRST
```

## The Workflow

1. `pythia impact <OBJECT> --depth 2` — everything that depends on it,
   as a tree, ending with the line that matters:
   `-- impact: N dependent objects, M currently VALID`.
2. `pythia deps <OBJECT> --depth 2` — the other direction, what it uses;
   read it when the change touches calls or table access.
3. `pythia invalid` — the baseline. Anything already INVALID before the
   change must not be blamed on the change later; apply compares against
   this automatically, but you should know the starting state too.

## Reading the numbers

| Result | What to do |
|---|---|
| 0 dependents | Say so and proceed; verify after apply anyway. |
| 1–9 dependents | List them to the developer alongside the proposal. |
| 10+ dependents, or any cross-schema dependent | Show the tree and get an explicit go-ahead **before writing any code**. |
| Dependents already INVALID | Point them out — the area is already unstable. |

Tables deserve the same treatment as code: `impact` on a table shows every
program that would be invalidated by an `ALTER`.

## Red Flags — STOP if you catch yourself thinking

| Thought | Reality |
|---------|---------|
| "It's just a small helper" | Helpers are the most-depended-on objects there are. |
| "I'll check impact after writing the code" | Then the number cannot change the design. Too late. |
| "The preview in apply shows impact anyway" | That line is confirmation of a number you already knew — not discovery. |
| "It's a new object, nothing depends on it" | True — say that, with `impact` output as evidence, not as an assumption. |

## Hand-off

Impact known and acceptable → `pythia-write` to write the change following
the codebase's conventions → `pythia-apply` to land it with preview and
verification.
