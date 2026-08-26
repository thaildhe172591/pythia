#!/usr/bin/env python3
"""Self-checks for `pythia install` and the packaging metadata. No database,
no network, no npx needed.

Run: python tests/test_install.py
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pythia  # noqa: E402


def test_template_matches_the_example_file():
    """The embedded template and examples/connections.example.json must be the
    same document — the example file is the reviewable source of truth."""
    example = (ROOT / "examples" / "connections.example.json").read_text(encoding="utf-8")
    assert json.loads(pythia.CONNECTIONS_TEMPLATE) == json.loads(example)


def test_scaffold_creates_config_once_and_never_clobbers():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        path, created = pythia.scaffold_config(root)
        assert created
        assert path == root / ".pythia" / "connections.json"
        assert json.loads(path.read_text(encoding="utf-8"))  # valid JSON on disk
        path.write_text('{"default": "prod"}', encoding="utf-8")
        path2, created2 = pythia.scaffold_config(root)
        assert path2 == path and not created2
        assert json.loads(path.read_text(encoding="utf-8")) == {"default": "prod"}


def test_missing_npx_falls_back_to_the_bundled_pack():
    """No Node -> the whole kit still installs. One target only
    (.agents/skills, the universal layout) — a second copy in
    .claude/skills would double every skill in Claude Code's menu. The
    copy merges: foreign skills survive, and stale plsql-* copies of the
    pack's old names are cleaned from both conventional roots."""
    import shutil
    old = shutil.which
    shutil.which = lambda name: None
    try:
        assert pythia.run_skills_add("thaildhe172591/pythia") is None
    finally:
        shutil.which = old
    with tempfile.TemporaryDirectory() as td:
        agents = pathlib.Path(td) / ".agents" / "skills"
        claude = pathlib.Path(td) / ".claude" / "skills"
        # pre-existing content: a custom skill, and stale old-name copies
        (agents / "my-team-skill").mkdir(parents=True)
        (agents / "my-team-skill" / "SKILL.md").write_text("x", encoding="utf-8")
        (agents / "plsql-apply").mkdir()
        (claude / "plsql-review").mkdir(parents=True)
        targets = pythia.copy_bundled_skills(td)
        assert targets == [agents]
        assert (agents / "pythia-apply" / "SKILL.md").is_file()
        assert (agents / "pythia-review" / "reference" / "antipatterns.md").is_file()
        assert not (claude / "pythia-apply").exists()      # single target
        assert (agents / "my-team-skill" / "SKILL.md").read_text(
            encoding="utf-8") == "x"                        # merge, not wipe
        assert not (agents / "plsql-apply").exists()        # legacy cleaned
        assert not (claude / "plsql-review").exists()       # in both roots
        pythia.copy_bundled_skills(td)                      # idempotent


def test_invocation_names_the_dash_m_form():
    """`python -m pythia` runs with argv[0] = a site-packages path nobody
    typed; the printed follow-ups must use the -m form instead."""
    import types
    real_main = sys.modules.get("__main__")
    fake = types.ModuleType("__main__")
    fake.__spec__ = types.SimpleNamespace(name="pythia")
    sys.modules["__main__"] = fake
    try:
        interp = pathlib.Path(sys.executable).stem
        assert pythia.invocation() == f"{interp} -m pythia"
    finally:
        sys.modules["__main__"] = real_main


def test_pack_dirs_resolve_in_the_source_layout():
    assert (pythia.QUERY_DIR / "impact.sql").is_file()
    assert (pythia.SKILLS_DIR / "pythia-apply" / "SKILL.md").is_file()


def test_one_version_across_every_manifest():
    """pyproject, plugin.json, marketplace.json and npm/package.json must all
    carry the same version — the release workflow publishes from one tag."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    v = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert f'version = "{v}"' in pyproject
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["metadata"]["version"] == v
    npm = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    assert npm["version"] == v
    assert 'name = "pythia-plsql"' in pyproject
    assert 'pythia = "pythia:main"' in pyproject
    assert "oracledb" in pyproject


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
