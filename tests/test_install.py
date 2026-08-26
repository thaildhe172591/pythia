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
    """No Node -> the whole kit still installs, into .claude/skills ONLY —
    the directory Claude Code reliably reads (field evidence: project
    .agents/skills is invisible to some versions, and any second copy
    doubles every menu entry). Foreign skills survive; stale plsql-* and
    old .agents pack copies are cleaned."""
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
        (agents / "my-team-skill").mkdir(parents=True)
        (agents / "my-team-skill" / "SKILL.md").write_text("x", encoding="utf-8")
        (agents / "pythia-apply").mkdir()      # stale copy from 0.2.0-0.2.3
        (claude / "plsql-review").mkdir(parents=True)
        targets = pythia.copy_bundled_skills(td)
        assert targets == [claude]
        assert (claude / "pythia-apply" / "SKILL.md").is_file()
        assert (claude / "pythia-review" / "reference" / "antipatterns.md").is_file()
        assert not (agents / "pythia-apply").exists()       # single copy
        assert (agents / "my-team-skill" / "SKILL.md").read_text(
            encoding="utf-8") == "x"                         # merge, not wipe
        assert not (claude / "plsql-review").exists()        # legacy cleaned
        pythia.copy_bundled_skills(td)                       # idempotent


def test_copy_survives_a_symlinked_destination():
    """`npx skills add` leaves .claude/skills/<name> as a symlink into
    .agents/skills/<name>. Copying through that link and then cleaning the
    target left dangling links and no pack — it happened on a real machine."""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        agents = base / ".agents" / "skills" / "pythia-explore"
        claude = base / ".claude" / "skills"
        agents.mkdir(parents=True)
        (agents / "SKILL.md").write_text("old", encoding="utf-8")
        claude.mkdir(parents=True)
        try:
            (claude / "pythia-explore").symlink_to(agents,
                                                   target_is_directory=True)
        except (OSError, NotImplementedError):
            return          # no symlink privilege on this box; skip
        pythia.copy_bundled_skills(td)
        real = claude / "pythia-explore" / "SKILL.md"
        assert real.is_file(), "pack vanished through the symlink"
        assert not (claude / "pythia-explore").is_symlink()
        assert "old" not in real.read_text(encoding="utf-8")


def test_global_pack_detection_skips_project_copies():
    with tempfile.TemporaryDirectory() as home:
        assert not pythia.global_pack_present(home)
        d = pathlib.Path(home) / ".claude" / "skills" / "pythia-apply"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x", encoding="utf-8")
        assert pythia.global_pack_present(home)


def test_skills_add_cmd_scopes():
    """Unattended project installs pin one agent (no double-install);
    global installs use -g; the interactive picker stays untouched."""
    assert pythia.skills_add_cmd("npx", "o/r", interactive=False) == \
        ["npx", "skills", "add", "o/r", "-y", "-a", "claude-code"]
    assert pythia.skills_add_cmd("npx", "o/r", interactive=False, glob=True) == \
        ["npx", "skills", "add", "o/r", "-g", "-y"]
    assert pythia.skills_add_cmd("npx", "o/r", interactive=True) == \
        ["npx", "skills", "add", "o/r"]


def test_pack_dirs_resolve_in_the_source_layout():
    assert (pythia.QUERY_DIR / "impact.sql").is_file()
    assert (pythia.SKILLS_DIR / "pythia-apply" / "SKILL.md").is_file()


def test_agent_user_json_saves_the_same_password_it_prints():
    """The agent flow: one --json --save run must be self-consistent — the
    password inside the SQL is the password written to connections.json."""
    import argparse
    import contextlib
    import io
    import os
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / ".pythia").mkdir()
        (root / ".pythia" / "connections.json").write_text(json.dumps(
            {"dev": {"host": "h", "port": 1521, "service_name": "s",
                     "user": "OWNER", "password": "p", "schema": "OWNER"}}),
            encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(td)
        try:
            ns = argparse.Namespace(conn=None, json=True, save=True, name=None)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pythia.cmd_agent_user(None, None, ns)
            d = json.loads(buf.getvalue())
            assert d["saved_connection"] == "dev_agent"
            assert d["password"] in d["sql"]
            cfg = json.loads((root / ".pythia" / "connections.json")
                             .read_text(encoding="utf-8"))
            assert cfg["dev_agent"]["password"] == d["password"]
            assert cfg["dev"]["password"] == "p"        # owner untouched
        finally:
            os.chdir(cwd)


def test_devnull_stdin_is_not_a_human():
    """The gate must hold for a real subprocess, not just in theory. On
    Windows NUL is a character device and isatty() says True for it, so a
    stdin=DEVNULL child would otherwise self-approve writes."""
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "PYTHIA_CI"}
    with tempfile.TemporaryDirectory() as td:
        (pathlib.Path(td) / ".pythia").mkdir()
        (pathlib.Path(td) / ".pythia" / "connections.json").write_text(
            json.dumps({"dev": {"host": "h", "user": "U", "password": "p",
                                "schema": "U"}}), encoding="utf-8")
        env["PYTHONPATH"] = str(ROOT / "scripts")   # -m pythia from the repo
        r = subprocess.run(
            [sys.executable, "-m", "pythia", "policy", "set", "structural",
             "allow"],
            cwd=td, env=env, stdin=subprocess.DEVNULL, text=True,
            capture_output=True)
        assert r.returncode != 0, r.stdout + r.stderr
        assert "developer" in (r.stdout + r.stderr).lower()
        assert not (pathlib.Path(td) / ".pythia" / "policy.json").is_file()


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
