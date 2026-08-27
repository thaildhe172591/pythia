---
name: pythia-skill-author
description: Use when the developer wants their own way of working captured as a new skill - "make a skill for how we do X", a house convention or preference worth teaching every future session, a team ritual, or the same correction arriving for the second time. Interviews the developer, mines the real conventions from the database, and writes a skill in this pack's format.
---

# Authoring a New Skill

**Announce at start:** "Using pythia-skill-author — let's capture how you actually work."

**Phase:** Learn → Ask → Do — interview, mine the schema, verify triggering

A skill is a decision captured so it never has to be re-argued. Capture what
the team *actually does* — with evidence from the database — not what anyone
remembers about it.

## Step 1 — Interview, one question at a time

Ask, in order, waiting for each answer:

1. **What task is this for?» One skill per concern — "our report procedure
   ritual" is one skill; "everything about our team" is not.
2. **When should it trigger?** Collect the developer's *own words* — the
   phrases they would type ("làm báo cáo", "clone the export proc", an error
   code, a file pattern). These go into the description verbatim; triggering
   lives or dies on them.
3. **Which parts are law, which are taste?** Hard rules get an Iron Law and
   a red-flags table; preferences get a "default unless told otherwise".
4. **What does *wrong* look like?** Past incidents make the best red-flag
   rows.

## Step 2 — Mine the evidence

Memory lies; the schema does not. Before writing a line:

- `pythia similar <TYPICAL_NAME>` → which family of programs embodies this
  workflow; open the top hits with `pythia src` and extract the *actual*
  naming, parameter and structure conventions.
- `pythia args` / `pythia cols` for the signatures and types the skill will
  tell people to use.
- Save two or three short **verbatim** snippets as examples — real code
  outranks invented code.

## Step 3 — Draft in the house format

- Frontmatter: kebab-case `name` matching the folder; `description` that
  opens with the trigger condition (`Use when ...`) and contains the
  developer's phrases from step 1.
- Body budget ~150 lines. Long material (templates, checklists, snippet
  libraries) goes to `reference/` and is linked, not pasted.
- Structure menu — use what the answers call for, skip the rest:
  Iron Law (one line, caps) · numbered workflow · red-flags table
  ("thought → reality") · "When NOT to use" · an **Announce at start** line.
- Write in the language the team works in; this pack is English, a private
  team skill may be Vietnamese — the developer decides.

**Not everything needs a skill.** Pure naming and style rules travel better
as `.pythia/conventions.json` (machine-checked at every apply preview) plus
`.pythia/conventions.md` (prose the agent reads). Reserve skills for
workflows — things with steps, gates and judgment.

## Step 4 — Place it

User skills live in the **developer's project**, e.g.
`.claude/skills/<name>/SKILL.md` for Claude Code (other agents: the layout
`skills/<name>/SKILL.md` works with `npx skills add`). Never write into the
installed pythia pack — updates would overwrite it, and the pack's lint
enforces its own fixed skill list.

## Step 5 — Verify it triggers

In a **fresh session**, give a task phrased the way the developer would
really ask (step 2's phrases). The skill must activate unprompted. If it
does not, the description is the bug: sharpen it with the exact phrases that
failed, and test again.

## Keep it alive

When the developer corrects the agent for the same thing twice, propose
folding that correction into the skill — that is the skill earning its keep.
Retire rules that stopped being true; a stale skill is worse than none.

## Before sharing outside the team

Run the hygiene check this pack applies to itself: no hosts, schema names,
credentials, or internal identifiers in the skill or its references. What is
fine in a private repo is a leak in a public one.

## Red Flags — STOP if you catch yourself thinking

| Thought | Reality |
|---------|---------|
| "I'll write it from memory of this chat" | Mine `similar`/`src` first — the codebase is the authority. |
| "One big skill covering everything" | One concern per skill, or nothing triggers cleanly. |
| "Paste the whole style guide in" | Budget is ~150 lines; long material goes to reference/. |
| "The description can be generic" | Generic descriptions never trigger. Use the developer's own phrases. |
| "It works, no need to test triggering" | Untested triggering = a skill nobody ever sees again. |
