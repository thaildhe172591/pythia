---
name: using-pythia
description: Use when starting any conversation that touches Oracle Database or PL/SQL - it establishes how to pick the right pythia skill BEFORE any response or action, including clarifying questions. Fires on plain requests in any language, no slash command needed - "build/add/fix/explain/review/deploy X", "lam/them/sua/tao/giai thich/kiem tra/trien khai X" (Vietnamese, with or without diacritics). Also use whenever unsure which pythia skill applies, or on catching yourself answering an Oracle question with none open.
---

# Using pythia — the pocket handbook

**Phase:** Learn → Ask → Do — this page opens the right one

pythia is the main book; this is the small notebook clipped inside its cover.
Its one job is consistency: whether a session asks the right questions should
not depend on the agent's mood that day.

## The Rule

If there is even a small chance a pythia skill applies, open it **before
acting** — before answering, before exploring, before clarifying questions.
Opened the wrong one? Close it, no harm done. Skipping the check is how
sessions drift: some ask, some silently decide.

## Route by what the developer said

| The request sounds like | Open first | Then |
|---|---|---|
| "build / add / change X" — and any decision is open | `pythia-spec` — the questions are mandatory, the spec/plan documents are offered and skippable | impact → write → apply |
| "explain X / why / how does it work" | `pythia-explore` | |
| "is it safe to touch X" | `pythia-impact` | |
| "create a table / add a column / comment the columns" ("tạo bảng", "thêm cột") | `pythia-table` | deploy |
| "write a package / procedure / trigger" ("viết package") | `pythia-package` | write → apply |
| "change this procedure" — a small edit, spec settled | `pythia-impact`, then `pythia-write` | apply |
| "it's slow / optimise this query" ("chậm", "tối ưu") | `pythia-tune` | table or write |
| "review this" | `pythia-review` | |
| "land / apply this" — one statement | `pythia-apply` | |
| "run this script / deploy the release" ("chạy script DDL") | `pythia-deploy` | |
| "do it all without asking me" ("tự động hoàn toàn") | `pythia-autopilot` — one lease, never a blank cheque | spec first |
| about to say done / fixed / faster | `pythia-verify` — evidence first | |
| "our standards / adopt this base's style" | `pythia-conventions` | |
| "make a skill for how we do X" | `pythia-skill-author` | |
| setup, connection failures, privilege warnings, approvals not arriving | `pythia-setup` (`pythia doctor`) | |
| about to write a comment in code you are changing | `pythia-comment` — one line, or it belongs somewhere else | |
| an ORA-/PLS- error to investigate | `pythia-explore` (`errors`, `src`) | `pythia-review` |

Priority when several apply: **process before construction** — spec before
write, impact before apply, verify before "done". Announce the skill you
opened ("Using pythia-spec — ...") so the developer can see which page you
are on. The session-start STATE lines say whether an autopilot lease is
active — read them before deciding whether a write needs an approval.

## Working beside other packs

This notebook assumes nothing else is installed. If a general process pack
(brainstorming, TDD, planning) is active in the session, let it own generic
process and keep pythia's Oracle gates — they compose. Never treat its
presence as a reason to skip `pythia-spec`, nor pythia's presence as a
reason to skip it.

## Red flags — the exact thoughts behind "some sessions ask, some don't"

| Thought | Reality |
|---------|---------|
| "This task is simple" | Simple tasks hide spec. A forgot-password flow carried seven unasked decisions. |
| "I remember what that skill says" | Skills evolve. Open the current page. |
| "I'll just look around first" | `pythia-explore` IS how you look around. Route first. |
| "Another pack will do the asking" | If it fires, good. If it does not, `pythia-spec` is the floor. |
| "Asking would stall the developer" | Paying for an unasked decision stalls harder. |

## No skill support on this platform?

`pythia guide` prints the full contract; `pythia guide --brief` is this
notebook's first page, sized for a session preamble. AGENTS.md carries the
standing rules. The handbook does not require a skills-capable agent.

---

*The pattern — a router consulted before any action — is Jesse Vincent's
Superpowers, rebuilt from scratch for this kit.*
