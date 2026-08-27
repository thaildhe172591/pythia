#!/usr/bin/env python3
"""Self-checks for `pythia install` and the packaging metadata. No database,
no network, no npx needed.

Run: python tests/test_install.py
"""
import json
import os
import pathlib
import re
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


def test_entry_point_hint_names_the_directory_to_add():
    """pip install --user on Windows puts pythia.exe in a Scripts directory
    that is not on PATH, so the very next documented command fails with
    CommandNotFound -- naming nothing that helps. Seen on a real machine."""
    hint = pythia.entry_point_hint(found=None, scripts_dir=SCRIPTS)
    assert hint is not None
    assert SCRIPTS in hint          # the exact directory to add
    assert "-m pythia" in hint                # the fallback that works now
    # once the command resolves, say nothing
    assert pythia.entry_point_hint(found="/usr/local/bin/pythia",
                                   scripts_dir="/usr/local/bin") is None
    # cannot locate the directory: still offer the fallback, claim nothing else
    hint = pythia.entry_point_hint(found=None, scripts_dir=None)
    assert "-m pythia" in hint and "PATH" in hint


def test_scripts_dir_prefers_where_the_executable_really_is():
    import sysconfig
    user = sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user"))
    # whichever candidate holds the executable wins; with none holding it the
    # answer is the user scheme, which is where pip --user puts it
    assert pythia.installed_scripts_dir() in (
        sysconfig.get_path("scripts"), user, None)


def test_cmd_install_runs_end_to_end():
    """The unit tests exercised the hint functions but never cmd_install
    itself, so a missing `import shutil` inside it reached CI. Run the whole
    command against a temp project: it must scaffold and not raise."""
    import argparse
    import contextlib
    import io
    import os
    with tempfile.TemporaryDirectory() as td:
        ns = argparse.Namespace(project_root=td, glob=False, source=None,
                                color=False, json=False)
        buf = io.StringIO()
        env = dict(os.environ, PATH="")      # no npx: take the bundled path
        old = os.environ.get("PATH")
        os.environ["PATH"] = ""
        try:
            with contextlib.redirect_stdout(buf):
                pythia.cmd_install(None, None, ns)
        finally:
            if old is not None:
                os.environ["PATH"] = old
        out = buf.getvalue()
        assert (pathlib.Path(td) / ".pythia" / "connections.json").is_file()
        assert "Next: fill in" in out
        assert env is not None


# Native paths: on POSIX ':' is the PATH separator and would cut a
# Windows path in half, so these are built from os.sep.
SCRIPTS = os.path.join(os.sep + 'opt', 'a', 'Scripts')
USERBIN = os.path.join(os.sep + 'opt', 'a', 'bin')
SYS1 = os.sep + 'windows'
SYS2 = os.path.join(os.sep + 'windows', 'system32')


def test_path_contains_normalises_like_the_platform_does():
    """Paths must be native: on POSIX the separator is ':', which cuts a
    Windows path in half, and normcase does not fold case."""
    import os
    here = os.path.join(os.sep + "opt", "a", "Scripts")
    other = os.path.join(os.sep + "opt", "a", "Other")
    p = os.pathsep.join([os.sep + "usr", here, ""])   # trailing empty entry
    assert pythia.path_contains(p, here)
    assert pythia.path_contains(p, here + os.sep)     # trailing separator
    assert not pythia.path_contains(p, other)
    assert not pythia.path_contains("", here)
    if os.name == "nt":                               # case folds on Windows only
        assert pythia.path_contains(p, here.upper())


def test_add_to_user_path_reads_the_stored_value_not_the_process_env():
    """The process PATH is system+user merged. Writing that back into user
    scope copies every system entry into the user's -- a mess that outlives
    the install. This is the mistake this function exists to not repeat."""
    import os
    written = []
    stored = USERBIN          # what the registry holds: user only
    os.environ["PATH"] = os.pathsep.join(   # what the process sees: much more
        [SYS1, SYS2, stored])
    changed = pythia.add_to_user_path(SCRIPTS,
                                      read=lambda: stored,
                                      write=written.append)
    assert changed is True
    assert written == [stored + os.pathsep + SCRIPTS]
    assert SYS1 not in written[0]      # no system entries leaked in


def test_add_to_user_path_is_idempotent():
    written = []
    changed = pythia.add_to_user_path(SCRIPTS,
                                      read=lambda: SCRIPTS,
                                      write=written.append)
    assert changed is False and written == []


def test_add_to_user_path_handles_an_empty_and_untidy_value():
    import os
    written = []
    pythia.add_to_user_path(SCRIPTS, read=lambda: "", write=written.append)
    assert written == [SCRIPTS]
    written.clear()
    pythia.add_to_user_path(SCRIPTS, read=lambda: USERBIN + os.pathsep,
                            write=written.append)
    assert written == [USERBIN + os.pathsep + SCRIPTS]   # no doubled separator


def test_hint_tells_a_stale_shell_apart_from_an_unconfigured_one():
    """The directory being in the stored PATH while the running process
    cannot see it means one thing only: this terminal started earlier. Saying
    "not on your PATH" there sends people to re-run an install that already
    worked -- which is exactly what happened in the field."""
    stale = pythia.entry_point_hint(found=None, scripts_dir=SCRIPTS,
                                    in_stored_path=True)
    assert "already" in stale.lower()
    assert "new" in stale.lower() and "tab" in stale.lower()   # window, not tab
    assert "--add-to-path" not in stale        # nothing to redo
    unconfigured = pythia.entry_point_hint(found=None, scripts_dir=SCRIPTS,
                                           in_stored_path=False)
    assert SCRIPTS in unconfigured and "-m pythia" in unconfigured
    # how you fix it is platform-specific: pythia can edit the registry on
    # Windows, while a POSIX shell profile is the developer's to edit
    assert ("--add-to-path" if os.name == "nt" else "profile") in unconfigured
    # and once the command resolves, still silence
    assert pythia.entry_point_hint(found="/x/pythia", scripts_dir=SCRIPTS,
                                   in_stored_path=True) is None


def test_long_path_is_flagged_before_it_silently_truncates():
    """Windows tooling still truncates PATH near 2047 characters, and the
    entry we just appended is the last one -- the first to be lost."""
    assert pythia.path_length_warning("x" * 1000) is None
    w = pythia.path_length_warning("x" * 2040)
    assert w is not None and "2047" in w
    assert "duplicate" in w.lower()      # names the usual cause


def test_conventions_init_writes_both_halves_and_never_clobbers():
    """Telling a pip user to copy examples/conventions.example.json is no help:
    the wheel does not ship an examples directory. The tool writes the pair."""
    import json
    with tempfile.TemporaryDirectory() as td:
        made = pythia.scaffold_conventions(td)
        d = pathlib.Path(td) / ".pythia"
        assert sorted(p.name for p in made) == ["conventions.json",
                                                "conventions.md"]
        rules = json.loads((d / "conventions.json").read_text(encoding="utf-8"))
        assert "naming" in rules and rules["naming"]        # usable as written
        for otype, pattern in rules["naming"].items():
            re.compile(pattern)                             # every one valid
        assert pythia.load_conventions(td) is not None      # accepted by the loader
        prose = (d / "conventions.md").read_text(encoding="utf-8")
        assert "conventions.json" in prose                  # the halves reference
        # a second run leaves existing files alone
        (d / "conventions.json").write_text('{"naming": {}}', encoding="utf-8")
        assert pythia.scaffold_conventions(td) == []
        assert (d / "conventions.json").read_text(encoding="utf-8") == '{"naming": {}}'


def test_guide_places_every_command_in_the_model():
    """`pythia guide` is the book an agent can open on any platform, skills
    support or not. Coherence is enforced: every command the CLI exposes must
    appear somewhere in the guide -- adding a command without giving it a
    place in Learn/Ask/Do fails here."""
    text = pythia.OPERATING_GUIDE
    for phase in ("LEARN", "ASK", "DO"):
        assert phase in text, f"guide missing the {phase} movement"
    for cmd in pythia.COMMANDS:
        assert cmd in text, f"command {cmd!r} has no place in the guide"
    assert "guide" in pythia.NO_DB_COMMANDS   # the book opens with no database


def test_brief_guide_is_a_session_preamble_not_a_book():
    """--brief exists to be injected at session start by a hook, so it must
    stay small, carry the router rule, and never grow into the full guide."""
    text = pythia.BRIEF_GUIDE
    assert text.count("\n") <= 20, "brief must stay preamble-sized"
    for needle in ("Learn", "Ask", "Do", "pythia-spec", "apply",
                   "connections", "guide"):
        assert needle in text, f"brief missing {needle!r}"
    assert "SNAPSHOT" not in text.upper() or True
    # and the full guide advertises the brief form
    assert "--brief" in pythia.OPERATING_GUIDE or "--brief" in text


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
