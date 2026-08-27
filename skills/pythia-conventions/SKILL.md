---
name: pythia-conventions
description: Use when a project's house style already exists somewhere and needs to become something the tooling can check - the developer hands over a standards document, points at an older or company base schema whose naming should be adopted, says "our team does it this way", or asks why an apply preview warned about a name. Derives the patterns from the real schema, verifies them against it, and writes .pythia/conventions.json and conventions.md.
---

# Adopting a Project's Conventions

**Announce at start:** "Using pythia-conventions — I'll read the patterns off the schema first."

**Phase:** Learn → Ask — derive from the schema, ask where the document disagrees, only then write the config

Conventions already exist in every mature schema. They are in the object
names, in a standards document, or in a senior developer's head. This turns
them into two files: patterns the tool checks on every write, and prose every
future agent session reads before writing.

## The Iron Law

```
NO PATTERN WRITTEN DOWN BEFORE THE SCHEMA HAS AGREED WITH IT
```

A pattern that does not match the objects already there is not a convention.
It is a guess that will warn on every apply until someone deletes it.

## The Workflow

1. **Read the shape off the database, not off your memory of it.**

   ```bash
   pythia conventions --scan
   ```

   It tokenises every object name and proposes a pattern per type. Do this
   even when a document exists — and never page through thousands of names
   yourself: that is what the command is for, and your context is better
   spent elsewhere.

2. **Read the developer's document, if there is one.** A base system's
   standards, a wiki page, an older project's rules. Look for what a regex
   cannot hold: parameter prefixes a calling layer depends on, a column every
   query must filter by, where a transaction may commit, which date or money
   representation is canonical. Those go in the prose half.

3. **Reconcile, and say so when the two disagree.** The document states
   intent; the schema states fact. A rule in the document that the schema
   contradicts is one of three things — a rule nobody follows, a rule for new
   code only, or drift worth reporting. Ask the developer which; do not
   silently pick.

4. **Write `.pythia/conventions.json`.** Start from the scan output, narrowed
   by the document. `pythia conventions --init` writes a blank pair if you
   want the skeleton first.

5. **Verify — this step is the point of the skill.**

   ```bash
   pythia conventions --check
   ```

   It reports coverage per type and names what misses.

   | Result | What it means |
   |---|---|
   | every name matches | the pattern is real; keep it |
   | ~90%+ with a handful of misses | genuine exceptions — list them in step 6 |
   | below 90% | **the pattern is wrong**, not the schema; widen or split it, then check again |
   | nothing of that type | an untested rule; keep it only if new objects of that type are expected |

6. **Record the exceptions, with their reason.** Objects that break the rule
   on purpose — ported names from a base platform, legacy entry points whose
   names are part of a public surface. Say why renaming would cost more than
   the warning. An unexplained exception gets "fixed" by the next person.

7. **Write `.pythia/conventions.md`.** For every rule, state **the cost of
   breaking it**, not just the rule. "Parameter prefixes must be `b_`/`a_`"
   is ignorable; "the calling layer binds by prefix, so a wrong one silently
   unbinds the field" is not. Rules with named consequences get followed.

## Adopting from an older base the team already runs

Point the connection at that schema and run steps 1 and 2 there — a base
system with thousands of objects is the most reliable statement of a house
style that exists, far better than anyone's recollection of it. Then switch
the connection to the new project and run `--check`: coverage tells you how
much of the base style the new schema has actually inherited.

## Red Flags — STOP if you catch yourself thinking

| Thought | Reality |
|---------|---------|
| "I'll write the patterns from the document alone" | Documents describe intent; schemas record what happened. Scan first. |
| "80% coverage is good enough" | Every miss warns on every apply. Fix the pattern or record the exception. |
| "I'll read all the object names to work it out" | `--scan` does that without spending your context. |
| "The schema disagrees, so the document is wrong" | It may be a new-code-only rule. Ask. |
| "A rule per line is enough" | A rule without its cost is a rule people skip. |

## When NOT to use this skill

- Writing one object in an existing style → `pythia-write`, which reads these
  files and uses `similar` for the details.
- Capturing a *workflow* with steps and gates → `pythia-skill-author`.
  Naming and style belong here; procedures belong in a skill.
