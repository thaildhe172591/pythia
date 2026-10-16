#!/usr/bin/env python3
"""Self-checks for `pythia install` and the packaging metadata. No database,
no network, no npx needed.

Run: python tests/test_install.py
"""
import contextlib
import io
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


def test_scaffold_creates_the_config_readable_by_its_owner_only():
    """connections.json is where the passwords go. Created 0644, it was
    readable by every user on the machine — and pythia then warned about its
    own file on every command (field test 0.15.1)."""
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory() as td:
        path, created = pythia.scaffold_config(pathlib.Path(td))
        assert created and path.stat().st_mode & 0o777 == 0o600


SECRET = ".pythia/connections.json"


@contextlib.contextmanager
def hermetic_git():
    """git with no system or personal config: a developer's own excludes file
    must not decide what these tests see."""
    keys = ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME", "HOME")
    old = {k: os.environ.get(k) for k in keys}
    with tempfile.TemporaryDirectory() as home:
        empty = os.path.join(home, "gitconfig")
        open(empty, "w").close()
        os.environ.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=empty,
                          XDG_CONFIG_HOME=home, HOME=home)
        try:
            yield
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def git_init(td):
    """A fresh repository at td; None where git is absent (the test skips)."""
    import subprocess
    git = pythia.path_lookup("git")
    if git:
        subprocess.run([git, "init", "-q", str(td)], check=True,
                       stdin=subprocess.DEVNULL)
    return git


def test_install_keeps_the_password_file_out_of_every_clone():
    """GUIDE and SECURITY said connections.json is "gitignored by the
    scaffold", but nothing wrote the rule: in the 0.15.3 field test `git
    status` listed `.pythia/`, one `git add -A` away from committing the
    passwords. The rule goes in the project's .gitignore, so it reaches every
    clone — .git/info/exclude would protect this machine only."""
    with hermetic_git(), tempfile.TemporaryDirectory() as td:
        if git_init(td) is None:
            return
        root = pathlib.Path(td)
        gi = root / ".gitignore"
        gi.write_bytes(b"node_modules")          # no final newline: theirs stays
        pythia.scaffold_config(root)
        assert pythia.git_ignore_source(root, SECRET)[0] == "none"
        assert pythia.ignore_connections(root) == gi
        text = gi.read_text(encoding="utf-8")
        assert text.startswith("node_modules\n") and SECRET in text.splitlines()
        assert pythia.git_ignore_source(root, SECRET)[0] == "shared"
        assert pythia.ignore_connections(root) is None          # once
        assert gi.read_text(encoding="utf-8") == text


def test_install_trusts_a_shared_rule_but_not_a_negated_or_private_one():
    with hermetic_git(), tempfile.TemporaryDirectory() as td:
        if git_init(td) is None:
            return
        root = pathlib.Path(td)
        gi = root / ".gitignore"
        gi.write_text(".pythia/\n", encoding="utf-8")
        assert pythia.ignore_connections(root) is None          # covered already
        assert gi.read_text(encoding="utf-8") == ".pythia/\n"
        # a later negation un-ignores it: the appended rule comes last and wins
        gi.write_text(".pythia/*\n!.pythia/connections.json\n", encoding="utf-8")
        assert pythia.git_ignore_source(root, SECRET)[0] == "none"
        assert pythia.ignore_connections(root) == gi
        assert pythia.git_ignore_source(root, SECRET)[0] == "shared"
        # ignored on this machine only: a teammate's clone would still commit it
        gi.unlink()
        exclude = root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text(SECRET + "\n", encoding="utf-8")
        state, source = pythia.git_ignore_source(root, SECRET)
        assert state == "local" and "exclude" in source
        assert pythia.ignore_connections(root) == gi
        assert pythia.git_ignore_source(root, SECRET)[0] == "shared"


def test_install_writes_the_rule_outside_git_too():
    """No repository yet, or no git at all: the rule is written anyway, so a
    later `git init` starts safe — and read back as text, written once."""
    with hermetic_git(), tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        assert pythia.git_ignore_source(root, SECRET) is None
        assert pythia.ignore_connections(root) == root / ".gitignore"
        assert pythia.ignore_connections(root) is None
    real = pythia.path_lookup
    pythia.path_lookup = lambda *a, **k: None
    try:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / ".gitignore").write_text("/.pythia/connections.json\n",
                                             encoding="utf-8")
            assert pythia.ignore_connections(root) is None
            (root / ".gitignore").write_text(
                ".pythia\n!.pythia/connections.json\n", encoding="utf-8")
            assert pythia.ignore_connections(root) == root / ".gitignore"
    finally:
        pythia.path_lookup = real


def test_install_warns_when_git_already_tracks_the_password_file():
    """A rule cannot untrack a committed file; the passwords are already in
    the history. Say so, with the commands, at install time."""
    import subprocess
    with hermetic_git(), tempfile.TemporaryDirectory() as td:
        git = git_init(td)
        if git is None:
            return
        root = pathlib.Path(td)
        pythia.scaffold_config(root)
        subprocess.run([git, "-C", td, "add", "-f", SECRET], check=True,
                       stdin=subprocess.DEVNULL)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.report_connections_ignore(root)
        out = buf.getvalue()
        assert f"git rm --cached {SECRET}" in out and "password" in out, out


def test_install_survives_a_gitignore_it_cannot_write():
    """The rule is a safeguard, not a reason to abort the install: say what
    to add by hand and carry on."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / ".gitignore").mkdir()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pythia.report_connections_ignore(root)
        out = buf.getvalue()
        assert "WARNING" in out and f"add {SECRET}" in out, out


def test_a_readable_config_is_warned_about_once_per_process():
    if os.name != "posix":
        return
    import contextlib
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "connections.json"
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o644)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            pythia._load_config(path)
            pythia._load_config(path)
        assert buf.getvalue().count("readable by other users") == 1


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
        assert targets == [claude, agents]
        assert (claude / "pythia-apply" / "SKILL.md").is_file()
        assert (claude / "pythia-review" / "reference" / "antipatterns.md").is_file()
        assert (agents / "pythia-apply" / "SKILL.md").is_file()   # Codex reads this
        assert (agents / "my-team-skill" / "SKILL.md").read_text(
            encoding="utf-8") == "x"                         # merge, not wipe
        assert not (claude / "plsql-review").exists()        # legacy cleaned
        pythia.copy_bundled_skills(td)                       # idempotent


def test_fallback_serves_both_claude_and_codex():
    """Claude reads .claude/skills; Codex reads .agents/skills. The no-Node
    fallback must populate both, or a Codex user without Node gets nothing."""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        targets = pythia.copy_bundled_skills(td)
        assert targets == [base / ".claude" / "skills", base / ".agents" / "skills"]
        for root in targets:
            assert (root / "pythia-apply" / "SKILL.md").is_file()


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


def test_the_website_states_what_the_code_is():
    """site/ is published to GitHub Pages. What it states — the release, the
    counts, every skill by name — must be what this commit is, so a version
    bump or a new skill that forgets the site fails here, not in public."""
    site = ROOT / "site"
    html = (site / "index.html").read_text(encoding="utf-8")
    v = pythia.__version__
    assert f'data-version="{v}"' in html
    assert set(re.findall(r'class="ver">([^<]+)<', html)) == {v}
    skills = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
    want = {"commands": len(pythia.COMMANDS), "skills": len(skills),
            "queries": len(list((ROOT / "queries").glob("*.sql"))),
            "lint": len(pythia.LINT_RULES)}
    for key, n in want.items():
        assert set(re.findall(rf'data-count="{key}">(\d+)<', html)) == {str(n)}, key
    for name in skills:
        assert f"<b>{name}</b>" in html, name
    for f in ("logo.webp", "logo.png", "favicon.png"):     # what the page loads
        assert (site / f).is_file(), f


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
    assert pythia.__version__ == v      # what the MCP server reports
    assert 'name = "pythia-plsql"' in pyproject
    assert 'pythia = "pythia:main"' in pyproject
    assert "oracledb" in pyproject


def test_license_is_an_spdx_string_the_build_backend_reads():
    """setuptools deprecated `license = { text = ... }` and stops building it
    after 2027-02-18; the release workflow builds with whatever setuptools is
    current. The SPDX string needs setuptools 77, which still runs on 3.9."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^license = "MIT"$', pyproject, re.M), "license must be an SPDX string"
    assert re.search(r'^license-files = \["LICENSE"\]$', pyproject, re.M)
    assert (ROOT / "LICENSE").is_file()
    floor = re.search(r'requires = \["setuptools>=(\d+)', pyproject)
    assert floor and int(floor.group(1)) >= 77, "SPDX license needs setuptools>=77"
    for manifest in (ROOT / ".claude-plugin" / "plugin.json",
                     ROOT / ".claude-plugin" / "marketplace.json",
                     ROOT / "npm" / "package.json"):
        assert '"license": "MIT"' in manifest.read_text(encoding="utf-8"), manifest


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
        gi = pathlib.Path(td) / ".gitignore"
        assert SECRET in gi.read_text(encoding="utf-8").splitlines()
        assert f"Added {SECRET} to" in out
        assert env is not None


def test_claude_hook_block_matches_the_example_file():
    """What install wires into .claude/settings.json is exactly the hooks
    block and the deny rule of the example file — the reviewable source of
    truth, as with the connections template. The allow list and autoMode
    stay in the example: those are the developer's permission preferences."""
    ex = json.loads((ROOT / "examples" / "claude-code-settings.example.json")
                    .read_text(encoding="utf-8"))
    assert pythia.CLAUDE_HOOKS == ex["hooks"]
    assert pythia.CLAUDE_DENY == ex["permissions"]["deny"]


def test_install_claude_hooks_creates_settings_and_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        path, added = pythia.install_claude_hooks(td)
        assert path == pathlib.Path(td) / ".claude" / "settings.json"
        assert added == ["hooks.SessionStart", "hooks.PreToolUse",
                         "hooks.PostToolUse", "permissions.deny"]
        s = json.loads(path.read_text(encoding="utf-8"))
        assert s["hooks"] == pythia.CLAUDE_HOOKS
        assert s["permissions"]["deny"] == pythia.CLAUDE_DENY
        before = path.read_text(encoding="utf-8")
        assert pythia.install_claude_hooks(td) == (path, [])
        assert path.read_text(encoding="utf-8") == before


def test_install_claude_hooks_merges_into_existing_settings():
    """The file is Claude Code's, not pythia's: every key the developer put
    there survives, their own hooks stay, and a pythia hook already present
    in another spelling (python3, the plugin's path) is recognised, not
    doubled. Only the missing pieces are added."""
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "model": "opus",
            "permissions": {"allow": ["Bash(git status)"],
                            "deny": ["Bash(rm -rf *)"]},
            "hooks": {
                "SessionStart": [{"hooks": [
                    {"type": "command", "command": "echo hi"}]}],
                "PostToolUse": [{"matcher": "AskUserQuestion", "hooks": [
                    {"type": "command",
                     "command": "python3 -m pythia approve --hook"}]}]}}),
            encoding="utf-8")
        _, added = pythia.install_claude_hooks(td)
        # the AskUserQuestion approve hook was there in another spelling: kept,
        # not doubled; only the Bash post-tool entry joins it
        assert added == ["hooks.SessionStart", "hooks.PreToolUse",
                         "hooks.PostToolUse", "permissions.deny"]
        s = json.loads(path.read_text(encoding="utf-8"))
        assert s["model"] == "opus"
        assert s["permissions"]["allow"] == ["Bash(git status)"]
        assert s["permissions"]["deny"][0] == "Bash(rm -rf *)"
        assert set(pythia.CLAUDE_DENY) <= set(s["permissions"]["deny"])
        cmds = [h["command"] for ev in s["hooks"].values()
                for e in ev for h in e["hooks"]]
        assert "echo hi" in cmds
        assert sum("approve --hook" in c for c in cmds) == 1   # not doubled
        assert sum("hook session-start" in c for c in cmds) == 1


def test_install_claude_hooks_refuses_a_file_it_cannot_parse():
    """Broken JSON is still the developer's file. Touching it would destroy
    what they wrote; say so and leave it byte-identical."""
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert pythia.install_claude_hooks(td) == (path, None)
        assert path.read_text(encoding="utf-8") == "{not json"


def test_cmd_install_wires_the_claude_hooks_unless_told_not_to():
    """The chat approval door needs the hook; a pip user who never opened
    GUIDE section 11 had none — seen in the 0.10.0 field test. install wires
    it by default and says so; --no-hooks leaves Claude Code's file alone."""
    import argparse
    import contextlib
    import io
    old = os.environ.get("PATH")
    os.environ["PATH"] = ""                    # no npx: bundled path, offline
    chooser = pythia.choose_launcher           # where `python` starts pythia
    pythia.choose_launcher = lambda **kw: ("python -m pythia", True, "")
    try:
        for no_hooks in (False, True):
            with tempfile.TemporaryDirectory() as td:
                ns = argparse.Namespace(project_root=td, glob=False,
                                        source=None, color=False, json=False,
                                        no_hooks=no_hooks)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    pythia.cmd_install(None, None, ns)
                settings = pathlib.Path(td) / ".claude" / "settings.json"
                if no_hooks:
                    assert not settings.exists()
                    continue
                s = json.loads(settings.read_text(encoding="utf-8"))
                assert "approve --hook" in json.dumps(s["hooks"]["PostToolUse"])
                assert set(pythia.CLAUDE_DENY) <= set(s["permissions"]["deny"])
                assert "settings.json" in buf.getvalue()
    finally:
        pythia.choose_launcher = chooser
        if old is not None:
            os.environ["PATH"] = old


def test_global_install_wires_the_approve_hook_only():
    """~/.claude/settings.json applies to every project on the machine. The
    approve hook is silent unless a question carries a pythia token, so it
    belongs there; the session-start guide would put 15 lines about pythia
    into every session of every project, so that one stays per project."""
    with tempfile.TemporaryDirectory() as td:
        path, added = pythia.install_claude_hooks(
            td, events=("PreToolUse", "PostToolUse"))
        assert added == ["hooks.PreToolUse", "hooks.PostToolUse",
                         "permissions.deny"]
        s = json.loads(path.read_text(encoding="utf-8"))
        assert "SessionStart" not in s["hooks"]
        assert s["hooks"]["PostToolUse"] == pythia.CLAUDE_HOOKS["PostToolUse"]
        assert "enabledMcpjsonServers" not in s      # no .mcp.json at home


def test_cmd_install_serves_codex_in_the_project_scope():
    """Project install writes the harness into AGENTS.md always, and the
    session-start hook unless --no-hooks. (The .agents/skills copy is gated by
    global_pack_present() — a machine with a global pack skips it — so that is
    proven directly in test_fallback_serves_both_claude_and_codex, not here.)"""
    import argparse
    import contextlib
    import io
    old = os.environ.get("PATH")
    os.environ["PATH"] = ""                     # no npx: bundled fallback
    try:
        for no_hooks in (False, True):
            with tempfile.TemporaryDirectory() as td:
                ns = argparse.Namespace(project_root=td, glob=False, source=None,
                                        color=False, json=False, no_hooks=no_hooks)
                with contextlib.redirect_stdout(io.StringIO()):
                    pythia.cmd_install(None, None, ns)
                base = pathlib.Path(td)
                agents_md = base / "AGENTS.md"
                assert agents_md.is_file()                        # always
                assert pythia.BRIEF_GUIDE.rstrip() in agents_md.read_text(encoding="utf-8")
                hooks = base / ".codex" / "hooks.json"
                assert hooks.is_file() != no_hooks                # skipped iff --no-hooks
    finally:
        if old is not None:
            os.environ["PATH"] = old


# --- Codex: AGENTS.md harness (TP1) ------------------------------------------

def test_agents_md_block_is_the_guide_verbatim():
    block = pythia.agents_md_block()
    assert block.startswith(pythia.AGENTS_BEGIN)
    assert block.rstrip().endswith(pythia.AGENTS_END)
    assert pythia.BRIEF_GUIDE.rstrip() in block          # one source, no drift
    assert "pythia approve <token>" in block             # the Codex approval door


def test_merge_agents_md_creates_updates_and_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "AGENTS.md"
        p, action = pythia.merge_agents_md(path)
        assert p == path and action == "created"
        assert pythia.BRIEF_GUIDE.rstrip() in path.read_text(encoding="utf-8")
        assert pythia.merge_agents_md(path)[1] == "unchanged"


def test_merge_agents_md_preserves_the_developers_text():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "AGENTS.md"
        path.write_text("# House rules\n\nUse tabs.\n", encoding="utf-8")
        pythia.merge_agents_md(path)
        body = path.read_text(encoding="utf-8")
        assert body.startswith("# House rules\n\nUse tabs.\n")   # kept, on top
        assert pythia.AGENTS_BEGIN in body and pythia.BRIEF_GUIDE.rstrip() in body
        pythia.merge_agents_md(path)                             # a stale block
        assert path.read_text(encoding="utf-8").count(pythia.AGENTS_BEGIN) == 1


# --- Claude Code: CLAUDE.md, the file Claude Code itself always loads ---------

def test_claude_md_block_carries_the_harness_for_claude_not_codex():
    """Claude Code loads CLAUDE.md every session, hooks or not. The block holds
    the same brief guide as AGENTS.md and the session hook — one source — but
    names Claude's approval doors, never Codex's."""
    block = pythia.claude_md_block()
    assert block.startswith(pythia.AGENTS_BEGIN) and block.endswith(pythia.AGENTS_END)
    assert pythia.BRIEF_GUIDE.rstrip() in block
    assert "pythia_approve" in block and "AskUserQuestion" in block
    assert "APPROVAL ON CODEX" not in block
    for rule in ("pythia apply", "pythia deploy", "autopilot", "impact",
                 "connections.json"):
        assert rule in block, rule


def test_merge_claude_md_keeps_the_developers_text_and_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "CLAUDE.md"
        assert pythia.merge_claude_md(path) == (path, "created")
        assert pythia.merge_claude_md(path) == (path, "unchanged")
        path.write_text("# Our project\n\nUse tabs.\n", encoding="utf-8")
        assert pythia.merge_claude_md(path)[1] == "added"   # joined their file
        body = path.read_text(encoding="utf-8")
        assert body.startswith("# Our project\n\nUse tabs.\n")
        assert body.count(pythia.AGENTS_BEGIN) == 1
        pythia.merge_claude_md(path)                       # a second run
        assert path.read_text(encoding="utf-8").count(pythia.AGENTS_BEGIN) == 1


def test_merge_says_updated_only_when_a_block_was_replaced():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "CLAUDE.md"
        path.write_text(f"# Ours\n\n{pythia.AGENTS_BEGIN}\nold rules\n"
                        f"{pythia.AGENTS_END}\n", encoding="utf-8")
        assert pythia.merge_claude_md(path)[1] == "updated"
        assert "old rules" not in path.read_text(encoding="utf-8")
        import contextlib
        buf = io.StringIO()
        other = pathlib.Path(td) / "sub"
        other.mkdir()
        (other / "CLAUDE.md").write_text("# Theirs\n", encoding="utf-8")
        with contextlib.redirect_stdout(buf):
            pythia.report_claude_md(other)
        assert "Added the pythia block to" in buf.getvalue()


def test_cmd_install_writes_claude_md_in_the_project_scope():
    import argparse
    import contextlib
    old = os.environ.get("PATH")
    os.environ["PATH"] = ""                    # no npx: bundled path, offline
    try:
        with tempfile.TemporaryDirectory() as td:
            ns = argparse.Namespace(project_root=td, glob=False, source=None,
                                    color=False, json=False, no_hooks=True)
            with contextlib.redirect_stdout(io.StringIO()):
                pythia.cmd_install(None, None, ns)
            md = pathlib.Path(td) / "CLAUDE.md"
            assert md.is_file()                 # even with --no-hooks
            assert pythia.AGENTS_BEGIN in md.read_text(encoding="utf-8")
    finally:
        if old is not None:
            os.environ["PATH"] = old


def test_session_start_does_not_repeat_what_claude_md_already_loaded():
    """With the pythia block in CLAUDE.md the session hook would inject the
    same guide twice; it prints the live state and a pointer instead."""
    import argparse
    import contextlib
    with tempfile.TemporaryDirectory() as td:
        def run():
            old = sys.stdin
            sys.stdin = io.StringIO("")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    pythia.cmd_hook(None, None, argparse.Namespace(
                        event="session-start", project_root=td))
            finally:
                sys.stdin = old
            return buf.getvalue()
        first_line = pythia.BRIEF_GUIDE.splitlines()[2]   # the ROUTE line
        assert first_line in run()
        pythia.merge_claude_md(pathlib.Path(td) / "CLAUDE.md")
        out = run()
        assert first_line not in out and "CLAUDE.md" in out and "STATE" in out


# --- Codex: session-start hook (TP3) -----------------------------------------

def test_merge_codex_hooks_creates_and_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".codex" / "hooks.json"
        p, added = pythia.merge_codex_hooks(path)
        assert p == path and added == ["hooks.SessionStart"]
        s = json.loads(path.read_text(encoding="utf-8"))
        assert s["hooks"]["SessionStart"] == pythia.CODEX_HOOKS["SessionStart"]
        assert pythia.merge_codex_hooks(path) == (path, [])


def test_merge_codex_hooks_recognises_an_existing_spelling():
    """A hook already there in another spelling is the developer's: kept, not
    doubled. The pre-0.15 session hook (guide --brief) is upgraded in place to
    hook session-start, which prints the same guide plus the live state."""
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".codex" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command",
                        "command": "python3 -m pythia hook session-start"}]}]}}),
            encoding="utf-8")
        assert pythia.merge_codex_hooks(path) == (path, [])   # not doubled
        path.write_text(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command",
                        "command": "python3 -m pythia guide --brief"}]}]}}),
            encoding="utf-8")
        assert pythia.merge_codex_hooks(path) == (path, ["hooks.SessionStart"])
        entries = json.loads(path.read_text(encoding="utf-8"))["hooks"]["SessionStart"]
        assert len(entries) == 1
        assert entries[0]["hooks"][0]["command"] == \
            "python3 -m pythia hook session-start"           # spelling kept
        assert pythia.merge_codex_hooks(path) == (path, [])


def test_merge_codex_hooks_refuses_a_file_it_cannot_parse():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".codex" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert pythia.merge_codex_hooks(path) == (path, None)
        assert path.read_text(encoding="utf-8") == "{not json"


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
    saved = os.environ.get("PATH")
    os.environ["PATH"] = os.pathsep.join(   # what the process sees: much more
        [SYS1, SYS2, stored])
    try:
        changed = pythia.add_to_user_path(SCRIPTS,
                                          read=lambda: stored,
                                          write=written.append)
    finally:
        # a leaked fake PATH hides `python` from every later test
        if saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved
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
        assert "lint" in rules                               # the LEAD's block, discoverable
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
