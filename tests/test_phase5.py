#!/usr/bin/env python3
"""Phase 5 self-checks — the skill pack lint. No database, no agent needed.

Run: python tests/test_phase5.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

EXPECTED = {"pythia-setup", "pythia-explore", "pythia-impact",
            "pythia-write", "pythia-apply", "pythia-review",
            "pythia-skill-author", "pythia-conventions", "pythia-spec",
            "using-pythia"}

# spec: SKILL.md under 150 lines, detail pushed to reference/
MAX_LINES = 150


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    fields = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields


def test_all_expected_skills_exist():
    found = {p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()} \
        if SKILLS.is_dir() else set()
    assert found == EXPECTED, (f"missing: {sorted(EXPECTED - found)}, "
                               f"unexpected: {sorted(found - EXPECTED)}")


def test_frontmatter_is_valid_and_triggering():
    for name in sorted(EXPECTED):
        path = SKILLS / name / "SKILL.md"
        if not path.is_file():
            continue  # reported by test_all_expected_skills_exist
        text = path.read_text(encoding="utf-8")
        fm = frontmatter(text)
        assert fm is not None, f"{name}: no YAML frontmatter"
        assert fm.get("name") == name, f"{name}: frontmatter name must match folder"
        desc = fm.get("description", "")
        assert desc, f"{name}: description missing"
        assert desc.lower().startswith(("use when", "use before")), \
            f"{name}: description must open with its trigger condition " \
            "('Use when ...' or 'Use before ...')"
        assert len(desc) <= 1024, f"{name}: description too long ({len(desc)})"


def test_frontmatter_is_parseable_yaml():
    """A description holding an unquoted ": " is not valid YAML, and the
    agent harness then falls back to the H1 heading — the skill keeps its
    name but loses its trigger, so it silently stops firing. This shipped
    once; it does not ship twice. Checked without PyYAML: the suites run on
    the stdlib alone.
    """
    for name in sorted(EXPECTED):
        path = SKILLS / name / "SKILL.md"
        if not path.is_file():
            continue
        m = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"),
                     re.S)
        assert m, f"{name}: no YAML frontmatter"
        for line in m.group(1).splitlines():
            km = re.match(r"^([A-Za-z_][\w.]*):\s+(.*)$", line)
            if not km:
                continue            # continuation or nested line
            value = km.group(2)
            if value[:1] in "\"'":
                continue            # quoted: a colon inside is fine
            assert ": " not in value, (
                f"{name}: frontmatter '{km.group(1)}' holds an unquoted "
                f"': ' — YAML reads that as a nested mapping and the field "
                f"is lost. Rephrase or quote the value.")


def test_skill_bodies_stay_within_budget():
    for name in sorted(EXPECTED):
        path = SKILLS / name / "SKILL.md"
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        assert lines <= MAX_LINES, f"{name}: {lines} lines (max {MAX_LINES}); " \
                                   "push detail into reference/"


def test_apply_skill_keeps_the_honest_rollback_table():
    path = SKILLS / "pythia-apply" / "SKILL.md"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    for needle in ("Yes — completely", "Flashback Query", "Recycle Bin",
                   "Almost never", "exit 3", "run-sql"):
        assert needle in text, f"pythia-apply: missing {needle!r}"


def test_reference_files_exist_where_promised():
    for rel in ("pythia-explore/reference/data-dictionary.md",
                "pythia-write/reference/patterns.md",
                "pythia-review/reference/antipatterns.md"):
        assert (SKILLS / rel).is_file(), f"missing {rel}"


def test_readme_carries_the_required_tables():
    """The line cap is gone by the owner's call — the README now explains
    the operating model in full. What stays pinned is content: the drift
    table, the honest-rollback and policy tables, the install channels, and
    the Learn-Ask-Do frame the whole kit is organised around."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("1,016",                              # drift table
                   "Flashback Query", "Recycle Bin",     # rollback honesty
                   "plsql_source", "data_dml",           # policy table
                   "npx skills add",                     # install channel
                   "Learn", "Ask", "Do",                 # the operating model
                   "star-history.com",
                   "MIT"):
        assert needle in text, f"README missing {needle!r}"


def test_vietnamese_readme_carries_the_operating_model():
    text = (ROOT / "README.vi.md").read_text(encoding="utf-8")
    for needle in ("Học", "Hỏi", "Làm"):
        assert needle in text, f"README.vi missing {needle!r}"


def test_every_skill_declares_its_phase():
    """The kit's operating model is Learn - Ask - Do. A skill that cannot say
    which movement it serves is not part of the method, it is a loose page."""
    for name in sorted(EXPECTED):
        path = SKILLS / name / "SKILL.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        m = re.search(r"\*\*Phase:\*\* (.+)", text)
        assert m, f"{name}: no '**Phase:**' declaration"
        words = set(re.findall(r"Learn|Ask|Do", m.group(1)))
        assert words and words <= {"Learn", "Ask", "Do"}, \
            f"{name}: phase must name Learn/Ask/Do, got {m.group(1)!r}"


def test_no_manifest_hardcodes_a_stale_skill_count():
    """The pack grew from seven skills to eight and three shipped manifests
    still said seven. Counting in prose is a contradiction waiting to happen,
    so the check is: no manifest states a number the code can disprove."""
    import json
    n = len(EXPECTED)
    words = {7: "seven", 8: "eight", 9: "nine"}
    stale = [w for k, w in words.items() if k != n]
    for rel in (".claude-plugin/marketplace.json", "npm/package.json",
                "npm/README.md"):
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        for word in stale:
            assert word + " skill" not in text and word + "-skill" not in text, \
                f"{rel} claims {word} skills; there are {n}"
    mk = json.loads((ROOT / ".claude-plugin/marketplace.json")
                    .read_text(encoding="utf-8"))
    declared = {p.rsplit("/", 1)[-1] for p in mk["plugins"][0]["skills"]}
    assert declared == EXPECTED, (
        f"marketplace.json and the pack disagree: "
        f"missing {sorted(EXPECTED - declared)}, extra {sorted(declared - EXPECTED)}")


def test_apply_skill_teaches_the_approval_gate():
    """The CLI enforces the grant; the skill must explain it, or the agent
    reads the refusal as a malfunction and starts working around it."""
    text = (SKILLS / "pythia-apply" / "SKILL.md").read_text(encoding="utf-8")
    assert "pythia approve" in text
    for needle in ("cannot run it", "relay"):
        assert needle in text.lower(), f"apply skill missing {needle!r}"


def test_readme_and_security_carry_the_approval_gate():
    """The gate is the release's claim. If the README still describes a
    pipeline with no approval in it, the docs contradict the CLI."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "approve" in readme
    assert "token → approve → apply" in readme
    sec = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "Approval grant" in sec
    vi = (ROOT / "README.vi.md").read_text(encoding="utf-8")
    assert "approve" in vi


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except (Exception, SystemExit) as e:  # noqa: BLE001 — keep going
                failed += 1
                print(f"FAIL {name}: {e!r}")
    if failed:
        sys.exit(f"{failed} test(s) failed")
    print("OK")


if __name__ == "__main__":
    main()
