#!/usr/bin/env python3
"""Phase 5 self-checks — the skill pack lint. No database, no agent needed.

Run: python tests/test_phase5.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

EXPECTED = {"plsql-setup", "plsql-explore", "plsql-impact",
            "plsql-write", "plsql-apply", "plsql-review",
            "plsql-skill-author"}

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


def test_skill_bodies_stay_within_budget():
    for name in sorted(EXPECTED):
        path = SKILLS / name / "SKILL.md"
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        assert lines <= MAX_LINES, f"{name}: {lines} lines (max {MAX_LINES}); " \
                                   "push detail into reference/"


def test_apply_skill_keeps_the_honest_rollback_table():
    path = SKILLS / "plsql-apply" / "SKILL.md"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    for needle in ("Yes — completely", "Flashback Query", "Recycle Bin",
                   "Almost never", "exit 3", "run-sql"):
        assert needle in text, f"plsql-apply: missing {needle!r}"


def test_reference_files_exist_where_promised():
    for rel in ("plsql-explore/reference/data-dictionary.md",
                "plsql-write/reference/patterns.md",
                "plsql-review/reference/antipatterns.md"):
        assert (SKILLS / rel).is_file(), f"missing {rel}"


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
