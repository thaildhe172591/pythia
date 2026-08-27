---
name: pythia-spec
description: Use when the developer asks to build or change something and the request leaves real decisions open - a new feature or flow ("build X", "lam luong Y", "them chuc nang Z"), a behaviour with more than one reasonable shape, a security or audit trade-off, or scope that could be read two ways. The request arrives as plain words, not a command - this skill fires on the words. Surface those decisions as questions with options and trade-offs, get them settled, and only then move to impact and write. Also use the moment you catch yourself choosing a business behaviour mid-implementation that the developer never stated.
---

# Settling the Spec

**Announce at start:** "Using pythia-spec — this request has open decisions that are yours, not mine."

**Phase:** Learn → Ask — learn enough to ask well, ask until the spec is settled, and only then build

The other skills are the construction chain: they start after the spec is
settled. This skill owns the moment before — because an agent that quietly
settles the spec itself will build the wrong thing *fluently*, and the
developer's first chance to disagree arrives after the code exists, when
every choice costs a rewrite.

## The Iron Law

```
SPEC DECISIONS BELONG TO THE DEVELOPER. YOU PROPOSE; THEY CHOOSE.
```

## Spec or technical? The line that decides who decides

| Spec — ask first | Technical — decide yourself, cite your source |
|---|---|
| What is stored: new table vs new columns, what is kept vs deleted | Names and shapes — the conventions already decide them |
| Business behaviour: expiry times, retry limits, lockout counts, who is allowed | Which dictionary view or query answers a question |
| Security ↔ audit trade-offs: delete the token vs keep the trail | Following an existing pattern found via `similar` |
| Channels and scope: email or SMS, which tenants, which callers | Datatype dictated by the house rules for that kind of value |
| Deviating from a mandatory convention | Anything `pythia conventions` or the codebase answers |

Rule of thumb: **if two senior developers could reasonably ship different
behaviour, it is spec.** If the codebase or the conventions already answer
it, it is technical — decide, and show the căn cứ.

## The Workflow

1. **Learn enough to ask well — not enough to start building.** `cols` and
   `src` on the tables involved, `conventions`, `similar` for how this house
   solves it. The output of this step is *better questions*, not code.
2. **List every decision you would otherwise settle silently.** The test:
   walk your imagined implementation and note each point where you picked a
   behaviour the developer never stated.
3. **Ask, one at a time, options with trade-offs and a recommendation** —
   never an open "what do you want?". Each option says what it costs:
   *"OTP over SMS: 6 digits is guessable, so it forces an attempt counter
   and a lockout column — different table than the draft."*
4. **Write the settled spec back in a few lines** and get a nod. That
   paragraph is now the scope — `pythia-write`'s "the developer's sentence".
   This summary is not skippable; it is what was agreed.
5. **Offer the written artifacts — the developer chooses.** "Spec settled.
   Want it as a spec file and a step-by-step plan first, or build now?"
   Skipping is a legitimate answer; record it in one line and move on. The
   *questions* were the mandatory part — the documents never are.
6. **Hand off**: impact → write → apply, as always.

## Caught deciding mid-build?

Stop at that line. A new open decision discovered while coding is a new
question — asking it late is cheap; presenting it as a fait accompli in the
final report ("I also decided...") is the exact failure this skill exists to
prevent.

## Red Flags — STOP if you catch yourself thinking

| Thought | Reality |
|---------|---------|
| "The task is simple, asking would stall" | Simple requests hide the most spec: a forgot-password flow carries seven of them. Three questions cost a minute; unbuilding costs an afternoon. |
| "Lazy mode says don't stall on questions" | Lazy shortens the *solution*, never the understanding. Settling spec IS understanding. |
| "I'll flag my choices in the final report" | By then the developer can only agree or demand rework. That is not a choice, it is a bill. |
| "A brainstorming skill from another pack will handle it" | If one fired, follow it. If none did, this is the floor — the kit assumes nothing else is installed. |
| "The developer said 'just do it'" | That covers the decisions they could see. New ones you discover are still theirs. |
| "They skipped the spec file, so I can skip the questions" | Backwards. The questions are mandatory; only the documents are optional. |

## When NOT to use this skill

- The request is fully specified, or a bug with one defensible correct
  behaviour — go straight to `pythia-impact`.
- The developer already answered — do not re-ask what is settled; re-ask
  only what changed.
- Pure refactors that keep behaviour identical.
