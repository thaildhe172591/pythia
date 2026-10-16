#!/usr/bin/env python3
"""pythia — ask an Oracle Database directly instead of reading stale dumps.

Reads are plain commands; sql accepts SELECT/WITH only. Writes go through
`apply` (one statement) or `deploy` (a script) — snapshot, impact, preview,
apply, verify, report — gated by .pythia/policy.json and the developer's
approval, or by an autopilot lease the developer approved. There is no
--write flag: the write path is `apply`, nothing else.

Connection resolution order:
  1. --conn NAME
  2. PYTHIA_CONNECTION                (name of an entry in connections.json)
  3. PYTHIA_USER / PYTHIA_PASSWORD / PYTHIA_DSN   (+ optional PYTHIA_SCHEMA)
  4. .pythia/connections.json, searched upward from the current directory
     (override the file path with PYTHIA_CONFIG):
       - a single entry is used as-is
       - with several entries, the path segment directly under the project
         root picks one (root/DEV/anything -> DEV)
       - failing that, the entry named by a top-level "default": "<name>"
       - anything still ambiguous is an error, never a guess

Output is capped so large objects cannot swallow a context window; every cut
is announced with "-- truncated ..." (text) or "truncated": true (JSON), so
you always know whether you saw everything.

  pythia check
  pythia ls   "PKG_%"
  pythia src  MY_PACKAGE --body
  pythia args MY_PROCEDURE
  pythia ddl  TABLE MY_TABLE
  pythia cols MY_TABLE
  pythia grep "some_identifier"
  pythia sql  "select count(*) from all_views where owner = user"
  pythia invalid
  pythia errors MY_PACKAGE
  pythia deps MY_PACKAGE --depth 2
  pythia impact MY_TABLE
  pythia similar PKG_ORDER_TOTAL_LIST
  pythia plscope MY_TABLE
  pythia lint PKG_ORDER_BODY.sql
  pythia apply PKG_ORDER_BODY.sql
  pythia apply PKG_ORDER_BODY.sql --confirm 7f3a91
  pythia deploy release/
  pythia autopilot request --groups plsql_source,comments --hours 4
  pythia explain "select * from t_order where id = :id"
  pythia stats T_ORDER
  pythia comments T_ORDER --scaffold
  pythia doctor
  pythia journal list
  pythia journal restore <id>
  pythia policy
  pythia install
  pythia history MY_PACKAGE
  pythia unistr "Nhóm không được để trống"
  pythia agent-user --save
"""
import argparse
import json
import os
import pathlib
import re
import sys

__version__ = "0.15.9"     # kept equal to pyproject.toml (tests/test_install.py)

READONLY = re.compile(r"^\s*(select|with)\b", re.I)
CONFIG_DIR = ".pythia"
CONFIG_NAME = "connections.json"
INT_MAX = 2147483647

def _pack_dir(source_name, installed_name):
    """Repo layout first (reviewable dirs next to scripts/), wheel layout
    second (package data installed beside this module)."""
    here = pathlib.Path(__file__).resolve().parent
    source = here.parent / source_name
    return source if source.is_dir() else here / installed_name


QUERY_DIR = _pack_dir("queries", "pythia_queries")
SKILLS_DIR = _pack_dir("skills", "pythia_skills")

# Bind contract: what each query file is allowed to use. tests/test_phase2.py
# fails on any drift in either direction — that is how queries/ stays reviewable
# by PR without a database.
QUERY_BINDS = {
    "invalid-objects.sql": {"s"},
    "compile-errors.sql": {"s", "n"},
    "dependencies.sql": {"s", "n", "depth", "with_sys"},
    "impact.sql": {"s", "n", "depth"},
    "similar-candidates.sql": {"s"},
    "plscope-usages.sql": {"s", "n"},
    "plscope-statements.sql": {"s", "n"},
    "plscope-enabled.sql": {"s"},
    "source.sql": {"s", "n"},
    "object-source.sql": {"s", "n", "t"},
    "session-privileges.sql": set(),
    "current-schema.sql": set(),
    "name-occupants.sql": {"s", "n"},
    "object-names.sql": {"s"},
    "comment-current.sql": {"s", "t", "c"},
    "explain-display.sql": {"id", "fmt"},
    "table-stats.sql": {"s", "n"},
    "table-indexes.sql": {"s", "n"},
    "unindexed-fks.sql": {"s", "n"},
    "table-comments.sql": {"s", "n"},
    "db-version.sql": set(),
    "db-nls.sql": set(),
    "schema-settings.sql": {"s"},
}


# --- pure helpers (covered by tests/test_phase1.py) --------------------------

def is_readonly_sql(stmt):
    # WITH FUNCTION / WITH PROCEDURE (12c) is PL/SQL inside a query — it can
    # run an autonomous transaction past SET TRANSACTION READ ONLY
    if re.match(r"^\s*with\s+(function|procedure)\b", stmt or "", re.I):
        return False
    return bool(READONLY.match(stmt))


# --- terminal presentation ---------------------------------------------------

ANSI = {"red": "31", "green": "32", "yellow": "33", "cyan": "36",
        "bold": "1", "dim": "2"}


def color_enabled(stream=None, env=None):
    """ANSI color only for a human at a TTY. NO_COLOR (the standard) always
    wins; FORCE_COLOR opts back in; pipes stay plain so agents parse exactly
    what they saw."""
    env = os.environ if env is None else env
    if env.get("FORCE_COLOR"):
        return True    # explicit opt-in outranks the ambient opt-out below
    if env.get("NO_COLOR"):
        return False
    stream = sys.stdout if stream is None else stream
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text, color, enabled):
    if not enabled or not color:
        return text
    return f"\x1b[{ANSI[color]}m{text}\x1b[0m"


BLOCK_LOGO = """\
██████╗ ██╗   ██╗████████╗██╗  ██╗██╗ █████╗
██╔══██╗╚██╗ ██╔╝╚══██╔══╝██║  ██║██║██╔══██╗
██████╔╝ ╚████╔╝    ██║   ███████║██║███████║
██╔═══╝   ╚██╔╝     ██║   ██╔══██║██║██╔══██║
██║        ██║      ██║   ██║  ██║██║██║  ██║
╚═╝        ╚═╝      ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝"""


LOGO_STOPS = ((34, 211, 238), (167, 139, 250), (244, 114, 182))  # cyan → violet → pink
LOGO_256 = (51, 45, 39, 105, 141, 177)                            # same ramp, 256-color


def _logo_rgb(t):
    """Interpolate the gradient stops at t in [0, 1]."""
    seg = t * (len(LOGO_STOPS) - 1)
    i = min(int(seg), len(LOGO_STOPS) - 2)
    f = seg - i
    a, b = LOGO_STOPS[i], LOGO_STOPS[i + 1]
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


def banner(enabled, env=None):
    """A hello on `check`, TTY only — agents piping output never see it.
    Solid blocks get a diagonal color gradient; the box-drawing outline stays
    dim for depth. Truecolor when the terminal advertises it (COLORTERM, or
    WT_SESSION — Windows Terminal supports 24-bit but never says so),
    a 256-color ramp everywhere else."""
    if not enabled:
        return ""
    env = os.environ if env is None else env
    truecolor = (env.get("COLORTERM", "").lower() in ("truecolor", "24bit")
                 or bool(env.get("WT_SESSION")))
    lines = BLOCK_LOGO.splitlines()
    out = []
    for y, ln in enumerate(lines):
        width = max(len(ln), 1)
        chunk = []
        for x, ch in enumerate(ln):
            if ch == " ":
                chunk.append(ch)
            elif ch == "█":
                if truecolor:
                    r, g, b = _logo_rgb((x / width + y / len(lines)) / 2)
                    chunk.append(f"\x1b[38;2;{r};{g};{b}m{ch}\x1b[0m")
                else:
                    chunk.append(f"\x1b[38;5;{LOGO_256[min(y, len(LOGO_256) - 1)]}m{ch}\x1b[0m")
            else:
                chunk.append(f"\x1b[2m{ch}\x1b[0m")   # outline: dim, for depth
        out.append("".join(chunk))
    tag = paint("judgment for your agent's Oracle connection", "dim", enabled)
    return "\n" + "\n".join(out) + f"\n{tag}\n\n"


def paint_diff_line(ln, enabled):
    if ln.startswith(("+++", "---")):
        return paint(ln, "dim", enabled)
    if ln.startswith("+"):
        return paint(ln, "green", enabled)
    if ln.startswith("-"):
        return paint(ln, "red", enabled)
    if ln.startswith("@@"):
        return paint(ln, "cyan", enabled)
    return ln


NL = chr(10)   # a literal escape does not survive every editing path


def offer_path_fix(scripts_dir, interactive, forced=False, skip=False,
                   windows=None, add=None, ask=None):
    """Put pythia on PATH for the developer instead of handing them a command
    to run. Only ever touches the user PATH. A person at the console is asked
    (Enter means yes); with nobody to ask — an agent running the install,
    the usual case on a fresh machine — it is added, because otherwise
    `pythia` never works in the developer's own terminal. --no-path skips it."""
    windows = (os.name == "nt") if windows is None else windows
    add = add or add_to_user_path
    ask = ask or input
    if not scripts_dir or not windows or skip:
        return False
    if not (pathlib.Path(scripts_dir) / "pythia.exe").is_file():
        return False                      # a clone, a test run: nothing to put there
    if interactive and not forced:
        try:
            answer = ask(NL + "Add it to your PATH now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer not in ("", "y", "yes"):
            return False
    try:
        changed = add(scripts_dir)
    except OSError as e:
        print(f"Could not update your PATH: {e}")
        return False
    print(NL + f"  {'Added' if changed else 'Already in'} your user PATH: {scripts_dir}")
    warn = path_length_warning(os.environ.get("PATH", ""))
    if warn:
        print("  " + warn)
    print("  Open a NEW terminal for it to take effect — an already-running")
    print("  one keeps the environment it started with, and so do its tabs.")
    print(f"  Until then: {interpreter_word()} -m pythia <command>")
    return True


def path_length_warning(combined):
    """Windows tooling still truncates PATH around 2047 characters, and a
    freshly appended entry sits last -- so it is the first thing lost, and it
    is lost silently. The usual cause is a PATH that had the system entries
    copied into the user's."""
    n = len(combined or "")
    if n < 1900:
        return None
    return (f"Your PATH is {n} characters; Windows truncates near 2047, and the "
            "entry just added is last in line.\n"
            "  The usual cause is duplicate entries — the system PATH copied "
            "into the user PATH by\n"
            "  a `$env:PATH` one-liner. Compare the two scopes and remove what "
            "appears in both.")


def stored_path_has(directory):
    """Is the directory in the PATH as *stored*, rather than the one this
    process inherited? The two differ for every terminal opened before the
    last change, which is the common confusion."""
    if not directory or os.name != "nt":
        return False
    try:
        return path_contains(_read_user_path(), directory)
    except OSError:
        return False


def path_contains(path_value, directory):
    """Is this directory already listed in a PATH string? Compared the way
    the platform does: case-insensitively on Windows, ignoring trailing
    separators and empty entries."""
    want = os.path.normcase(os.path.normpath(str(directory)))
    return any(os.path.normcase(os.path.normpath(p)) == want
               for p in str(path_value).split(os.pathsep) if p.strip())


def _read_user_path():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        try:
            return winreg.QueryValueEx(k, "Path")[0]
        except FileNotFoundError:
            return ""


def _write_user_path(value):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Path", 0, winreg.REG_EXPAND_SZ, value)


def add_to_user_path(directory, read=None, write=None):
    """Append a directory to the *user* PATH, returning whether anything
    changed.

    Reads the stored user value, never `os.environ["PATH"]`. The process
    environment is the system and user values merged, so writing it back into
    user scope copies every system entry into the user's -- doubling the
    effective PATH and leaving a stale snapshot that shadows the real system
    one after it next changes. That mistake is easy to make and outlives the
    install, which is why this function exists rather than a one-line command.
    """
    read = read or _read_user_path
    write = write or _write_user_path
    current = read() or ""
    if path_contains(current, directory):
        return False
    joined = str(directory) if not current.strip() else \
        current.rstrip(os.pathsep) + os.pathsep + str(directory)
    write(joined)
    return True


def installed_scripts_dir():
    """The directory pip put our executable in. The default scheme is wrong
    for `pip install --user` — the common case on Windows — so the candidate
    that actually holds the executable wins."""
    import sysconfig
    candidates = [sysconfig.get_path("scripts")]
    try:
        candidates.append(
            sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user")))
    except (AttributeError, KeyError):      # very old Python: no user scheme
        pass
    exe = "pythia.exe" if os.name == "nt" else "pythia"
    for d in candidates:
        if d and (pathlib.Path(d) / exe).exists():
            return d
    return candidates[-1] if candidates else None


def entry_point_hint(found, scripts_dir, in_stored_path=False):
    """What to say when `pythia` will not resolve as a command. pip installs
    the executable into a scripts directory that is often not on PATH — the
    default for `pip install --user` on Windows — and the shell's
    CommandNotFound names nothing that helps. Seen on a real machine, at the
    step immediately after the install the docs tell you to run."""
    if found:
        return None
    if in_stored_path:
        # The directory is in the stored PATH but not in this process: the
        # terminal started before the change. Sending someone to re-run the
        # install here wastes their time -- it already worked.
        return (NL.join([
            "`pythia` is already on your PATH — this terminal just started "
            "before that was true.",
            "  Open a NEW terminal WINDOW. A new tab is not enough: tabs "
            "inherit the",
            "  environment of the window that spawned them, so if you use "
            "Windows Terminal,",
            "  VS Code or Cursor, restart the application itself.",
            "  In this one, keep using: "
            + interpreter_word() + " -m pythia <command>",
        ]))
    out = ["`pythia` is installed but not on your PATH."]
    if scripts_dir:
        out.append("  It lives in: " + str(scripts_dir))
        if os.name == "nt":
            # Deliberately not a PowerShell one-liner over $env:PATH:
            # that variable is system and user merged, so writing it
            # back into user scope copies every system entry into the
            # user's. Let the tool do it correctly instead.
            out.append("  Put it there with: " + interpreter_word()
                       + " -m pythia install --add-to-path")
        else:
            out.append("  Add it to your shell profile:")
            out.append("    export PATH=\"$PATH:" + str(scripts_dir) + "\"")
    else:
        out.append("  Add pip's scripts directory to PATH.")
    out.append("Either way, this works right now, PATH or no PATH:")
    out.append("  " + interpreter_word() + " -m pythia <command>")
    return "\n".join(out)


def _same_path(a, b):
    """The same file by spelling, symlinks NOT followed: a venv's python is a
    symlink to the base interpreter, and only the venv's own path loads the
    venv's packages."""
    norm = lambda p: os.path.normcase(os.path.abspath(p))      # noqa: E731
    return norm(a) == norm(b)


def interpreter_word():
    """This interpreter as a shell here can type it: its bare name when PATH
    finds this very file, else its absolute path, quoted. "python3" on most
    Linux/macOS, "python" on Windows — and in a venv nobody activated (Claude
    Code started from an app or an IDE), the venv's own path: the bare name
    would start the system interpreter, which has no pythia."""
    exe = sys.executable
    if not exe:
        return "python"
    # the name as typed: Path.stem would turn "python3.9" into "python3"
    name = re.sub(r"\.exe$", "", pathlib.Path(exe).name, flags=re.I)
    found = path_lookup(name)
    if found and _same_path(found, exe):
        return name
    return launcher_text([pathlib.Path(exe).as_posix()])


def invocation():
    """How to invoke this tool from a shell on this machine. Every printed
    command must be paste-able: a bare `pythia ...` is CommandNotFound for
    anyone running from source, or from a venv that is not on PATH."""
    main_spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if getattr(main_spec, "name", "") == "pythia":
        # `python -m pythia` — argv[0] is the module's site-packages path,
        # which nobody typed; the -m form is the paste-able one
        return f"{interpreter_word()} -m pythia"
    prog = sys.argv[0] or "pythia"
    if prog.lower().endswith(".py"):
        return f"{interpreter_word()} {launcher_text([prog]) if ' ' in prog else prog}"
    name = pathlib.Path(prog).name
    if not re.search(r"[/\\]", prog):
        return name                       # found by name: PATH has it
    found = path_lookup(name)
    if found and (_same_path(found, prog) or _same_path(found, prog + ".exe")):
        return name
    return f"{interpreter_word()} -m pythia"


HEADLESS_YES_MSG = (
    "--yes is the developer's flag, and no terminal is attached to this "
    "session.\nAgents preview, relay the diff and the impact verbatim, "
    "STOP, and pass --confirm <token> only after the developer's explicit "
    "approval in chat.\n(Real pipelines set PYTHIA_CI=1.)")


APPROVE_HEADLESS_MSG = (
    "approve is the developer's command — it needs a real console.\n"
    "Agents ask instead: AskUserQuestion with the text of `approve --card "
    "<token>` and the options Approve / Reject — the pythia hook mints the "
    "grant when the developer answers Approve — then the pythia_approve tool "
    "if that fails. Or a person runs approve in their own terminal.\n(Real "
    "pipelines apply with --yes and PYTHIA_CI=1 instead.)")


def grant_refusal(status, token, grant, conn_name, file_hint):
    """Why this write is refused, and the exact way to fix it. Each status
    gets its own sentence: 'no' without the reason teaches nothing."""
    approve_line = f"  {invocation()} approve {token}"
    preview_line = f"  {invocation()} apply {file_hint}"
    if status == "missing":
        return ("The confirmation token matches, but no developer approval is "
                "on file. Ask through the first door that works, moving on "
                "without asking when one fails:\n"
                + "\n".join(ln.replace("    ", "  ", 1) for ln in door_lines(token))
                + "\nThen re-run this command.")
    if status == "expired":
        return (f"That approval expired ({GRANT_TTL_MINUTES} minutes after it "
                "was given).\nAsk the developer to approve again:\n"
                f"{approve_line}")
    if status == "spent":
        return ("That approval was already used by a previous apply — one "
                "approval, one write.\nPreview again, then ask the developer "
                f"to approve the new token:\n{preview_line}")
    if status == "wrong_conn":
        return (f"That approval was given on connection "
                f"{grant.get('conn', '?')!r}, but this session targets "
                f"{conn_name!r}.\nApproving on one database does not approve "
                "another. On the right connection:\n"
                f"{preview_line} --conn {conn_name}")
    return "Refused: the developer approval on file is not valid."


def human_at_the_keyboard():
    """True only when a person is typing at a real console.

    isatty() alone is not enough on Windows: NUL is a character device, so
    a subprocess launched with stdin=DEVNULL reports isatty() == True and
    would sail straight through this gate. Only a real console answers
    GetConsoleMode, so ask that too. PYTHIA_CI=1 is the documented escape
    for real pipelines."""
    if os.environ.get("PYTHIA_CI"):
        return True
    try:
        if not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT
            mode = ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetConsoleMode(
                handle, ctypes.byref(mode)))
        except Exception:   # noqa: BLE001 — any failure means "not a console"
            return False
    return True


def console_session(ns=None):
    """A real console, with no environment escape.

    human_at_the_keyboard() honours PYTHIA_CI so real pipelines can apply
    with --yes. approve gets no such door: a pipeline that wants to write
    already has one, and a second escape would be the way around this gate.
    `ns.console` is the test seam and nothing sets it in production."""
    forced = getattr(ns, "console", None)
    if forced is not None:
        return bool(forced)
    try:
        if not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT
            mode = ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetConsoleMode(
                handle, ctypes.byref(mode)))
        except Exception:   # noqa: BLE001 — any failure means "not a console"
            return False
    return True


def forbid_write_flag(argv):
    if "--write" in argv:
        sys.exit(f"There is no --write flag. The write path is `{invocation()} "
                 "apply <file>` — snapshot, preview and verify included; "
                 "nothing else writes.")


def load_query(name):
    """Read a statement from queries/. All SQL lives there so it can be
    reviewed, tested and contributed to without reading Python."""
    path = QUERY_DIR / name
    if not path.is_file():
        sys.exit(f"Missing query file: {path}")
    return path.read_text(encoding="utf-8")


def query_binds(sql):
    """Bind names a statement actually uses, ignoring comments and string
    literals — a date format like 'hh24:mi:ss' is not two binds."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:[^']|'')*'", " ", sql)
    return set(re.findall(r"(?<![:\w]):([a-z_][a-z0-9_]*)", sql, re.I))


def find_config(cwd, env):
    """Return (config dict, project root) or (None, None).

    PYTHIA_CONFIG points straight at a JSON file; otherwise walk upward from
    cwd for .pythia/connections.json. The project root is the directory
    holding .pythia/ — path-based connection inference is anchored to it.
    """
    override = env.get("PYTHIA_CONFIG")
    if override:
        path = pathlib.Path(override)
        if not path.is_file():
            sys.exit(f"PYTHIA_CONFIG points to a missing file: {path}")
        root = path.parent.parent if path.parent.name == CONFIG_DIR else path.parent
        return _load_config(path), root
    for d in [cwd, *cwd.parents]:
        path = d / CONFIG_DIR / CONFIG_NAME
        if path.is_file():
            return _load_config(path), d
    return None, None


_MODE_WARNED = set()


def _load_config(path):
    if os.name == "posix":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077 and str(path) not in _MODE_WARNED:
            _MODE_WARNED.add(str(path))
            print(f"-- warning: {path} is readable by other users "
                  f"(mode {mode:o}); chmod 600 recommended", file=sys.stderr)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        sys.exit(f"Cannot parse {path}: {e}")


def resolve_connection(cfg, explicit, env, cwd, root):
    """Pick a connection. Precedence: --conn, PYTHIA_CONNECTION, PYTHIA_* env
    credentials, then the config file (single entry, the path segment directly
    under the project root, or the entry named by the top-level "default").
    Ambiguity is an error, never a guess."""
    cfg = dict(cfg or {})
    fallback = cfg.pop("default", None)
    if fallback is not None and not isinstance(fallback, str):
        example = next((k for k in cfg), "dev")
        sys.exit(f'"default" must name a connection, for example '
                 f'"default": "{example}" — got {json.dumps(fallback)}.')

    lookup = {}
    for k, v in cfg.items():
        if not isinstance(v, dict):
            sys.exit(f"Connection {k!r} in {CONFIG_NAME} must be an object of "
                     f"settings, got {json.dumps(v)}.")
        if k.upper() in lookup:
            sys.exit(f"Connection names collide ignoring case in {CONFIG_NAME}: "
                     f"{lookup[k.upper()][0]!r} vs {k!r}")
        lookup[k.upper()] = (k, v)
    names = ", ".join(sorted(k for k, _ in lookup.values()))

    if fallback and fallback.upper() not in lookup:
        sys.exit(f'"default" names {fallback!r}, which is not a connection. '
                 f"Available: {names or 'none'}.")

    wanted = explicit or env.get("PYTHIA_CONNECTION")
    if wanted:
        hit = lookup.get(wanted.upper())
        if not hit:
            sys.exit(f"Unknown connection {wanted!r}. Available: {names or 'none'}.")
        return hit

    if env.get("PYTHIA_USER") and env.get("PYTHIA_PASSWORD") and env.get("PYTHIA_DSN"):
        c = {"user": env["PYTHIA_USER"], "password": env["PYTHIA_PASSWORD"],
             "dsn": env["PYTHIA_DSN"]}
        if env.get("PYTHIA_SCHEMA"):
            c["schema"] = env["PYTHIA_SCHEMA"]
        return "env", c

    if not lookup:
        sys.exit(f"No connection configured. Create {CONFIG_DIR}/{CONFIG_NAME} "
                 "(see examples/connections.example.json) or set "
                 "PYTHIA_USER / PYTHIA_PASSWORD / PYTHIA_DSN.")

    if len(lookup) == 1:
        return next(iter(lookup.values()))

    if root is not None:
        try:
            seg = cwd.resolve().relative_to(pathlib.Path(root).resolve()).parts
        except ValueError:
            seg = ()
        if seg:
            hit = lookup.get(seg[0].upper())
            if hit:
                return hit

    # Nothing in the path to go on. The top-level "default" is a choice the user
    # wrote down rather than a guess, so it is safe to fall back to — and the
    # connection actually used is always echoed on stderr.
    if fallback:
        return lookup[fallback.upper()]

    misplaced = sorted(n for n, v in lookup.values() if "default" in v)
    if misplaced:
        sys.exit(f'Connection {misplaced[0]!r} has a "default" key inside it. '
                 f"The default belongs at the top level of {CONFIG_NAME}, "
                 f'alongside the connections: "default": "{misplaced[0]}"')

    sys.exit(f"Cannot infer a connection from {cwd}. Available: {names}.\n"
             "Use --conn NAME, set PYTHIA_CONNECTION, or name one at the top "
             f'level of {CONFIG_NAME}: "default": "<name>"')


def clip(rows, limit, offset=0):
    """Window a result; truncated=True whenever anything beyond the window exists."""
    end = offset + limit if limit else len(rows)
    return rows[offset:end], len(rows) > end


def filter_units(rows, body, spec):
    """rows: (type, line, text). --body keeps *BODY units, --spec the rest."""
    if body == spec:
        return rows
    return [r for r in rows if r[0].upper().endswith("BODY") == body]


def format_source(rows, raw):
    """Number source with Oracle's own ALL_SOURCE line values so they map 1:1
    to compiler `line N` positions; numbering restarts per unit (spec/body)."""
    if raw:
        return "".join(str(t).rstrip("\n") + "\n" for _, _, t in rows)
    multi = len({u for u, _, _ in rows}) > 1
    out, seen = [], None
    for unit, line, text in rows:
        if multi and unit != seen:
            if seen is not None:
                out.append("\n")
            out.append(f"-- {unit}\n")
            seen = unit
        s = str(text).rstrip("\n")
        out.append(f"{line:>6}  {s}\n")
    return "".join(out)


def format_errors(rows):
    """rows: (name, type, sequence, line, position, attribute, text).
    One header per object, then 'line:col SEVERITY message' per error."""
    out, seen = [], None
    for name, otype, _seq, line, pos, attr, text in rows:
        if (name, otype) != seen:
            out.append(f"{name} ({otype})\n")
            seen = (name, otype)
        out.append(f"  {line}:{pos} {attr} {str(text).strip()}\n")
    return "".join(out)


def render_tree(rows, root):
    """rows: (lvl, owner, name, type, ...) in hierarchical order; trailing
    columns are ignored so deps and impact share one renderer. The same object
    can appear on more than one path — that is the graph, not a bug."""
    out = [f"{root}\n"]
    for row in rows:
        lvl, owner, name, otype = row[:4]
        out.append(f"{'  ' * int(lvl)}{owner}.{name} ({otype})\n")
    return "".join(out)


def impact_summary(rows):
    """rows: (lvl, owner, name, type, status, dependency_type). Counts distinct
    objects — one object reached by three paths is still one object that has to
    recompile."""
    seen = {}
    for row in rows:
        _lvl, owner, name, otype, status = row[:5]
        seen[(owner, name, otype)] = status
    valid = sum(1 for s in seen.values() if s == "VALID")
    return f"-- impact: {len(seen)} dependent objects, {valid} currently VALID"


def rank_similar(target, candidates):
    """candidates: (object_name, object_type, status, last_ddl). A codebase's
    naming convention lives in the underscore-separated tokens of its names, so
    shared tokens are the cheapest honest signal of 'written the same way'.
    Returns each match with the shared tokens appended, best first."""
    target = str(target).upper()
    want = {t for t in target.split("_") if t}
    scored = []
    for row in candidates:
        name = str(row[0]).upper()
        if name == target:
            continue
        shared = want & {t for t in name.split("_") if t}
        if shared:
            scored.append((len(shared), name, (*row, " ".join(sorted(shared)))))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [row for _score, _name, row in scored]


def plscope_message(name, has_any_data):
    """What to say when an identifier lookup returns nothing. Never run the
    ALTER: recompiling a shared schema is the team's call, not the tool's."""
    if has_any_data:
        return (f"No PL/Scope entry for {name!r}. Either it does not exist, or the "
                "objects using it were compiled before PL/Scope was enabled.")
    return ("PL/Scope has no data for this schema, so this question cannot be "
            "answered exactly yet.\n"
            "To enable it, and then recompile the objects you care about:\n"
            "  ALTER SESSION SET plscope_settings='IDENTIFIERS:ALL, STATEMENTS:ALL';\n"
            "  ALTER PROCEDURE <name> COMPILE;\n"
            "Recompiling on a shared schema affects everyone using it — agree it with "
            "the team first. pythia will not run these for you.\n"
            f"Until then, the approximate answer is: {invocation()} grep \"<text>\"")


# --- write layer: pure decision functions (tests/test_phase3.py) -------------

GROUPS = ("plsql_source", "comments", "data_dml", "structural", "grants",
          "session")

# COMMENT ON TABLE / COLUMN: the previous comment is one dictionary read away,
# so unlike the rest of DDL it has a genuine undo. Other COMMENT ON targets
# (materialized views, operators...) stay structural.
COMMENT_ON_RE = re.compile(r"^comment\s+on\s+(table|column)\s+", re.I)

PLSQL_SOURCE_RE = re.compile(
    # NONEDITIONABLE, not "noeditionable": Oracle's keyword has the middle N,
    # and missing it dropped every NONEDITIONABLE object into structural —
    # deny by default, and counted by semicolons, so a PL/SQL body read as
    # "more than one statement". Both spellings are accepted here; only the
    # database decides whether the statement is valid.
    r"^create\s+(?:or\s+replace\s+)?(?:(?:non?)?editionable\s+|(?:no)?force\s+)*"
    r"(procedure|function|package\s+body|package|trigger|view|type\s+body|type)\b",
    re.I | re.S)


def skip_leading_noise(sql):
    """Drop leading whitespace and comments so classification sees the first
    keyword. Only leading ones: stripping comments globally would corrupt
    string literals like '-- not a comment'."""
    while True:
        sql = sql.lstrip()
        if sql.startswith("--"):
            nl = sql.find("\n")
            sql = "" if nl < 0 else sql[nl + 1:]
        elif sql.startswith("/*"):
            end = sql.find("*/")
            if end < 0:
                return ""
            sql = sql[end + 2:]
        else:
            return sql


def classify(sql):
    """Which policy group a statement belongs to, or "anonymous" for a bare
    BEGIN/DECLARE block (it can EXECUTE IMMEDIATE anything, so giving it a
    group would be self-deception), or None for anything unrecognized.
    Unrecognized means refused: a classifier that guesses generously is a
    classifier that lets deny be bypassed."""
    s = skip_leading_noise(sql)
    if re.match(r"^alter\s+session\b", s, re.I):
        return "session"                      # before the generic ALTER below
    # match the header with comments blanked: CREATE OR REPLACE /* x */ VIEW
    # is a view with a snapshot, not structural DDL without one
    if PLSQL_SOURCE_RE.match(mask_sql(s)):
        return "plsql_source"
    if re.match(r"^(insert|update|delete|merge)\b", s, re.I):
        return "data_dml"
    if re.match(r"^(grant|revoke)\b", s, re.I):
        return "grants"
    if COMMENT_ON_RE.match(s):
        return "comments"             # metadata with a real undo: own group
    if re.match(r"^(alter|drop|truncate|rename|create|comment)\b", s, re.I):
        return "structural"
    if re.match(r"^(begin|declare)\b", s, re.I):
        return "anonymous"
    return None


def parse_object(sql):
    """(type, name, schema|None) from a plsql_source statement. The parsed
    identity drives the snapshot — getting it wrong would snapshot the wrong
    object and silently destroy the only undo, hence the strict match."""
    s = skip_leading_noise(sql)
    masked = mask_sql(s)                      # same length: indexes carry over
    m = PLSQL_SOURCE_RE.match(masked)
    if not m:
        sys.exit("Cannot parse the object type from this CREATE statement.")
    otype = " ".join(m.group(1).upper().split())
    rest = masked[m.end():]
    ident = r'(?:"([^"]+)"|([A-Za-z][\w$#]*))'
    m2 = re.match(r"\s*" + ident + r"(?:\s*\.\s*" + ident + r")?", rest)
    if not m2:
        sys.exit(f"Cannot parse the {otype.lower()} name from this statement.")
    q1, p1, q2, p2 = m2.groups()
    first = q1 if q1 is not None else p1.upper()
    if q2 is None and p2 is None:
        return otype, first, None
    second = q2 if q2 is not None else p2.upper()
    return otype, second, first


def prepare_statement(sql, group):
    """What actually gets executed. A trailing line holding only / is a
    SQL*Plus directive, not SQL. The trailing ; belongs to a PL/SQL block but
    must go for everything else — specified per group because getting it
    backwards produces baffling compile errors. Content after the terminator
    means two statements in one file: refused, one object per file."""
    lines = sql.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "/":
        lines.pop()
    if any(ln.strip() == "/" for ln in lines):
        sys.exit("The file contains more than one statement (a / separator "
                 "remains mid-file). pythia apply takes exactly one statement "
                 "per file — split it.")
    text = "\n".join(lines).rstrip()
    if group != "plsql_source":
        body = text.rstrip(";").rstrip()
        # masked: 'a; b' inside a comment text or a DML literal is one
        # statement, and refusing it sent people to run it by hand
        if ";" in mask_literals(body):
            sys.exit("The file contains more than one statement. pythia apply "
                     "takes exactly one statement per file — split it.")
        return body
    return text


REVALIDATE_REFUSAL = """\
Refused: pythia cannot work out which rows this statement would touch, because
%s.
A data_dml write is approved on the rows it affects, so a row set that cannot
be measured is refused rather than guessed at.
Rewrite it as one UPDATE or DELETE over one table with one WHERE."""


def mask_literals(sql):
    """A same-length copy with quoted text and comments blanked to spaces.
    Length is the contract: an index found in the mask points at the same
    character in the original, which is what lets dml_probe trust a keyword
    scan without carrying a real parser."""
    out, i, n = [], 0, len(sql)
    while i < n:
        if sql[i] == "'":
            out.append(" ")
            i += 1
            while i < n:                    # a doubled '' simply closes and
                out.append(" ")             # reopens — blanked either way
                i += 1
                if sql[i - 1] == "'":
                    break
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                out.append(" ")
                i += 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(" " * (end - i))
            i = end
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def dml_probe(stmt):
    """(verb, target, predicate|None): the SELECT-able description of the rows
    an UPDATE or DELETE will touch. None for INSERT — nothing exists
    beforehand, so there is nothing to revalidate. Everything else is refused,
    for classify()'s reason: a parser that guesses generously is a parser that
    lets deny be bypassed.

    target is a span of text, not a parsed identifier, so a schema qualifier
    and an alias survive and a predicate written against the alias resolves."""
    s = skip_leading_noise(stmt)
    m = re.match(r"(insert|update|delete|merge)\b", s, re.I)
    verb = m.group(1).lower() if m else ""
    if verb == "insert":
        return None
    if verb == "merge":
        sys.exit("Refused: a MERGE cannot be revalidated — the rows it touches "
                 "are decided by a join against its source query, so pythia "
                 "cannot show you the set you would be approving.\n"
                 "Express it as an UPDATE and/or a DELETE, or run it by hand "
                 "with a DBA watching.")
    if re.search(r"q'", s, re.I):
        sys.exit(REVALIDATE_REFUSAL % "it uses an alternative-quoted (q'...') "
                                      "literal, which this parser does not read")
    masked = mask_literals(s)
    if re.search(r"\bselect\b", masked, re.I):
        sys.exit(REVALIDATE_REFUSAL % "it contains a subquery, so its row set "
                                      "is not one predicate over one table")
    wheres = [w.start() for w in re.finditer(r"\bwhere\b", masked, re.I)]
    if len(wheres) > 1:
        sys.exit(REVALIDATE_REFUSAL % "it has more than one WHERE, so which "
                                      "one selects the rows would be a guess")
    head = re.match(r"delete\s+(?:from\s+)?" if verb == "delete"
                    else r"update\s+", masked, re.I)
    start = head.end()
    if verb == "update":
        sets = re.search(r"\bset\b", masked[start:], re.I)
        if not sets:
            sys.exit(REVALIDATE_REFUSAL % "the UPDATE has no SET clause")
        end = start + sets.start()
    else:
        end = wheres[0] if wheres else len(masked)
    if wheres and wheres[0] < end:
        sys.exit(REVALIDATE_REFUSAL % "its WHERE comes before the table it "
                                      "reads, which this parser does not expect")
    target = s[start:end].strip()
    if not target:
        sys.exit(REVALIDATE_REFUSAL % "no table name follows the verb")
    pred = s[wheres[0] + 5:].strip() if wheres else None
    return verb, target, pred


def count_sql(target, pred):
    """The fingerprint query. Interpolated, not bound: a table name and a
    predicate are not bindable. The text comes from the very file whose DML is
    about to run, so this opens no trust boundary that statement does not open
    already — but it runs BEFORE approval, which SECURITY.md says out loud."""
    return ("select count(*) n, sum(ora_hash(rowidtochar(rowid))) s, "
            "min(ora_hash(rowidtochar(rowid))) lo, "
            "max(ora_hash(rowidtochar(rowid))) hi from "
            + target + (f" where {pred}" if pred else ""))


def sample_sql(target, pred):
    return ("select * from " + target + (f" where {pred}" if pred else "")
            + " fetch first :n rows only")


def measure_row_set(conn, target, pred, limit=10):
    """Count, fingerprint and sample the rows the statement will touch.
    Failing to measure is a refusal, never a reason to proceed: a write
    approved on a row set nobody could see is what this exists to prevent.

    ponytail: ROWID tracks set membership, not row contents — an in-place
    update to an approved row keeps its ROWID. Hash the sampled columns too if
    content drift ever needs catching."""
    try:
        _, agg = run_query(conn, count_sql(target, pred))
        cols, rows = run_query(conn, sample_sql(target, pred),
                               {"n": fetch_n(limit)})
    except Exception as e:            # the driver's exception class is imported
        sys.exit("Refused: pythia could not measure the rows this "  # in main()
                 f"statement would touch.\n  {target} — {e}\n"
                 "Revalidation is mandatory for UPDATE and DELETE, so a set "
                 "that cannot be measured cannot be approved.")
    n, s, lo, hi = (list(agg[0]) + [None] * 4)[:4] if agg else (0, 0, 0, 0)
    return {"target": target, "predicate": pred or "", "count": int(n or 0),
            "hash": f"{s or 0}/{lo or 0}/{hi or 0}",
            "columns": list(cols),
            "sample": [[cell(v) for v in r] for r in rows[:limit]],
            "truncated": len(rows) > limit}


def fingerprint_text(row_set):
    """What the token hashes. Empty when there is no row set, so plsql_source
    tokens stay byte-identical to the ones 0.8.0 printed."""
    if not row_set:
        return ""
    return f"rows={row_set['count']} hash={row_set['hash']}"


def render_row_set(row_set, en=False, indent="  "):
    """The rows an approval is about — printed identically by the preview and
    by approve, because what is approved must be what was shown."""
    n = row_set["count"]
    where = f" where {row_set['predicate']}" if row_set["predicate"] else ""
    out = [f"{indent}{paint(str(n) + ' rows', 'bold', en)} in "
           f"{row_set['target']}{where}"]
    if n > 1000:
        out.append(indent + paint("! more rows than anyone can read one by "
                                  "one — the count and the fingerprint are "
                                  "the whole of what you are approving",
                                  "yellow", en))
    if row_set["sample"]:
        out.append(indent + "\t".join(map(str, row_set["columns"])))
        out.extend(indent + "\t".join(map(str, r)) for r in row_set["sample"])
        if row_set["truncated"]:
            out.append(f"{indent}-- truncated at {len(row_set['sample'])} of "
                       f"{n} rows")
    return out


POLICY_DEFAULTS = {"plsql_source": "confirm", "comments": "confirm",
                   "data_dml": "deny", "structural": "deny", "grants": "deny",
                   "session": "allow"}

ROLLBACK_TABLE = """\
Is rollback real?  (this table also appears in README.md and pythia-apply)
  plsql_source  Yes - completely. Source is recoverable from ALL_SOURCE.
  comments      Yes - the previous comment is snapshotted before every write.
  data_dml      No. After commit only Flashback Query remains, within undo retention.
                Revalidation checks the row set before the write; it is not an undo.
  structural    Almost never. DROP COLUMN is permanent; a dropped table may be in the Recycle Bin.
  grants        Yes, but by hand.
  session       Not needed."""


def apply_token(object_type, name, file_text, db_source):
    """6 hex chars binding the write to what was previewed: file or database
    changing since the preview yields a different token, so what gets applied
    is exactly what was seen. A consistency check, not a secret — it is
    compared against one recomputed value, so length would cost usability and
    buy nothing."""
    import hashlib
    payload = "\n".join([object_type, name,
                         file_text.replace("\r\n", "\n"),
                         db_source.replace("\r\n", "\n")])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:6]


def effective_policy(raw):
    """Merge policy.json over the defaults, remembering where each value came
    from. Config is a trust boundary: unknown groups and values are refused
    with the accepted spelling, not ignored."""
    eff = {g: (v, "default") for g, v in POLICY_DEFAULTS.items()}
    for k, v in (raw or {}).items():
        if k not in POLICY_DEFAULTS:
            sys.exit(f"Unknown policy group {k!r} in policy.json. "
                     f"Groups: {', '.join(POLICY_DEFAULTS)}.")
        if v not in ("allow", "confirm", "deny"):
            sys.exit(f"Policy {k!r} must be allow, confirm or deny — got {v!r}.")
        eff[k] = (v, "policy.json")
    return eff


def policy_path(root):
    return pathlib.Path(root) / CONFIG_DIR / "policy.json"


def load_policy(root):
    path = policy_path(root)
    if not path.is_file():
        return effective_policy(None)
    try:
        return effective_policy(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as e:
        sys.exit(f"Cannot parse {path}: {e}")


def load_settings(root):
    """Optional .pythia/settings.json — an absent file means defaults."""
    path = pathlib.Path(root) / CONFIG_DIR / "settings.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        sys.exit(f"Cannot parse {path}: {e}")


def source_sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fetch_source(conn, schema, name):
    """Current source of an object, as ALL_SOURCE holds it. ('', None) when
    the name is not a source object (a table, say)."""
    _, rows = run_query(conn, load_query("source.sql"), {"s": schema, "n": name})
    if not rows:
        return "", None
    return "".join(cell(r[2]) for r in rows), rows[0][0]


def last_known_state(root, name):
    """What the database held, as far as the journal knows: the newest entry
    for this object, and the source that entry says was really there.
    A preview wrote nothing, so its `before` is the real state."""
    for eid in list_journal_entries(root):        # newest first
        e = read_journal_entry(root, eid)
        m = e["meta"]
        if str(m.get("object", "")).upper() != name.upper():
            continue
        real = e["after"] if (m.get("applied") or m.get("snapshot")) else e["before"]
        return real, eid, m
    return None, None, None


def auto_snapshot(conn, schema, name, ns):
    """Capture an object's source the moment an agent focuses on it, so a
    later hand-edit outside pythia still has something to go back to.

    Silent by design: writing to the journal costs the agent no context, and
    a line of "snapshot taken" on every read would cost it on every read.
    The one thing worth saying is drift — the source moved without an apply
    of ours explaining it — and that goes to stderr, so --json stays clean.

    Returns a drift warning, or None.
    """
    root = getattr(ns, "project_root", None)
    if root is None or not load_settings(root).get("auto_snapshot", True):
        return None
    try:
        text, otype = fetch_source(conn, schema, name)
        if not text.strip():
            return None                    # not a source object; nothing to keep
        known, eid, meta = last_known_state(root, name)
        if known is not None and source_sha(known) == source_sha(text):
            return None                    # unchanged — no entry, no output
        write_journal_entry(root, otype or "OBJECT", name.upper(), text, text,
                            {"schema": schema, "connection": ns.conn_name,
                             "snapshot": True, "applied": False,
                             "sha": source_sha(text)})
        if known is None:
            return None                    # first sighting is a baseline
        rollback = journal_root(root) / eid / "restore.sql"
        return (f"! {name.upper()} changed outside pythia since {eid}.\n"
                f"  Rollback file for the previous version: {rollback}\n"
                f"  All versions: {invocation()} history {name.upper()}")
    except Exception:                      # noqa: BLE001
        return None                        # a safety net must never break a read


def report_drift(msg):
    if msg:
        print(paint(msg, "yellow", color_enabled(sys.stderr)), file=sys.stderr)


def journaled_objects(root):
    names = {}
    for eid in list_journal_entries(root):        # newest first
        m = read_journal_entry(root, eid)["meta"]
        n = str(m.get("object", "")).upper()
        if n and n != "STATEMENT" and n not in names:
            names[n] = eid[:19]                   # the entry's timestamp
    return names


def undo_group_action(ns):
    """Undoing a CREATE is a DROP, which is `structural` — so the policy on
    that group decides whether the restore command we print can run."""
    return load_policy(ns.project_root)["structural"][0]


def journal_root(root):
    return pathlib.Path(root) / CONFIG_DIR / "journal"


# --- approval grants ---------------------------------------------------------
#
# The confirmation token proves the CONTENT did not move. A grant proves a
# HUMAN approved it. They are separate facts, so they are separate objects:
# the token is derived from the file and the database, the grant is minted by
# a person at a console (`pythia approve`) and spent by the apply that follows.

GRANT_TTL_MINUTES = 15


def grants_root(root):
    return pathlib.Path(root) / CONFIG_DIR / "grants"


TOKEN_RE = re.compile(r"[0-9a-z]{6,10}")


def valid_token(token):
    """Tokens name files: anything but 6-10 lowercase letters and digits is
    refused, so `../autopilot/lease` or an empty string can never read as an
    approval. (pythia prints hex: 6 a preview, 8 a lease, 10 a deploy.)"""
    return bool(TOKEN_RE.fullmatch(str(token or "")))


def grant_path(root, token):
    if not valid_token(token):
        raise ValueError(f"not a pythia token: {token!r}")
    return grants_root(root) / f"{token}.json"


def mint_grant(root, token, conn_name, now=None, revalidate=None,
               approver="console", session=None, batch=None):
    """Record one developer approval. Single use, short-lived, bound to the
    connection the preview ran on. `revalidate` is the row-set fingerprint the
    human was shown — audit data, not a second gate: the enforcement is the
    token, which already hashes that same fingerprint. `approver` says which
    door the human came through — console or chat — and `session` is the
    chat session's id; both are audit data too."""
    import datetime
    now = now or datetime.datetime.now()
    rec = {"token": token,
           "conn": conn_name or "",
           "minted_at": now.isoformat(timespec="seconds"),
           "expires_at": (now + datetime.timedelta(
               minutes=GRANT_TTL_MINUTES)).isoformat(timespec="seconds"),
           "used_at": None,
           "revalidate": revalidate or None,
           "approver": approver,
           "session": session or None,
           # a member of an approved deploy: valid only inside that deploy
           "batch": batch or None}
    d = grants_root(root)
    d.mkdir(parents=True, exist_ok=True)
    grant_path(root, token).write_text(json.dumps(rec, indent=2) + "\n",
                                       encoding="utf-8")
    return rec


def read_grant(root, token):
    """The grant on file, or None. A corrupt or half-written file is not an
    approval — it reads as absent, and absent refuses."""
    if not valid_token(token):
        return None
    p = grant_path(root, token)
    if not p.is_file():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return rec if isinstance(rec, dict) else None


def grant_status(grant, conn_name, now=None):
    """Why this grant does or does not authorize a write right now.

    Returns one of: ok, missing, expired, spent, wrong_conn. Each has its own
    refusal in apply, because "no" without the reason teaches nothing."""
    import datetime
    if not grant:
        return "missing"
    if grant.get("used_at"):
        return "spent"
    now = now or datetime.datetime.now()
    try:
        expires = datetime.datetime.fromisoformat(str(grant.get("expires_at")))
    except ValueError:
        return "expired"                    # unreadable stamp is not a licence
    if now > expires:
        return "expired"
    # connection names are typed by humans into connections.json; DEV and dev
    # are the same connection, and an approval must not hinge on the spelling
    if str(grant.get("conn", "")).upper() != str(conn_name or "").upper():
        return "wrong_conn"
    return "ok"


def spend_grant(root, token, now=None):
    """One apply per approval. Stamped in the same run that wrote."""
    import datetime
    rec = read_grant(root, token)
    if not rec:
        return
    rec["used_at"] = (now or datetime.datetime.now()).isoformat(
        timespec="seconds")
    grant_path(root, token).write_text(json.dumps(rec, indent=2) + "\n",
                                       encoding="utf-8")


def prune_expired_grants(root, now=None):
    """Expired grants are litter, not state: approve sweeps them on the way
    past. No prune command, no cron."""
    import datetime
    now = now or datetime.datetime.now()
    d = grants_root(root)
    if not d.is_dir():
        return 0
    gone = 0
    for p in sorted(d.glob("*.json")):
        rec = read_grant(root, p.stem)
        if rec is None or grant_status(rec, rec.get("conn"), now=now) == "expired":
            try:
                p.unlink()
                gone += 1
            except OSError:
                pass
    return gone


EDITIONABLE_KW_RE = re.compile(
    r"^create\s+(?:or\s+replace\s+)?((?:non?)?editionable)\b", re.I)


CREATE_HEAD_RE = re.compile(
    r"^(create\s+(?:or\s+replace\s+)?)((?:non?)?editionable\s+)?", re.I)


def reconcile_editionable(stmt, editionable):
    """Make the statement agree with the property the database already holds.

    ALL_SOURCE stores no CREATE header, so a body read back with `src` comes
    back bare and the keyword is the easiest thing in Oracle to lose — and
    Oracle refuses to *change* the property through CREATE OR REPLACE, so the
    statement cannot succeed. Left alone, ORA-38824 lands after the developer
    has already approved the preview. Nothing here is guessed:
    ALL_OBJECTS.EDITIONABLE is the answer, and the edit is announced.

    Returns (statement, note). The file on disk is never touched."""
    if editionable not in ("Y", "N"):
        return stmt, None      # a new object, or a type that has no edition
    body = skip_leading_noise(stmt)
    m = CREATE_HEAD_RE.match(body)
    if not m:
        return stmt, None
    have = (m.group(2) or "").strip().upper()
    if editionable == "N":
        want = "NONEDITIONABLE"
    else:
        # only NONEDITIONABLE contradicts an editionable object; a bare header
        # and an explicit EDITIONABLE both say the same thing to Oracle
        want = "" if have.endswith("EDITIONABLE") and have != "EDITIONABLE" else have
    if have == want:
        return stmt, None
    head = stmt[:len(stmt) - len(body)]
    fixed = head + m.group(1) + (want + " " if want else "") + body[m.end():]
    verb = "added" if want else "removed"
    # two lines, indented like the other preview warnings: one long line wraps
    # into noise in a terminal
    return fixed, (f"NONEDITIONABLE {verb} to match the object in the "
                   "database — Oracle cannot\n  change that property through "
                   "CREATE OR REPLACE (ORA-38824). Your file is unchanged.")


def create_prefix(after_text):
    """The CREATE header ALL_SOURCE does not store, rebuilt for the version
    held in the database. The editionable keyword is read off the file being
    applied, because the database's own answer is not in ALL_SOURCE and
    Oracle refuses to *change* the property through CREATE OR REPLACE
    (ORA-38824) — so a file that applies cleanly already spells the property
    the object has. Getting this wrong costs twice: a restore that fails
    exactly when it is needed, and a phantom line in the diff the developer
    approves."""
    m = EDITIONABLE_KW_RE.match(skip_leading_noise(after_text))
    return "CREATE OR REPLACE " + (f"{m.group(1).upper()} " if m else "")


def source_body(after_text):
    """The ALL_SOURCE-shaped half of a stored statement. create_prefix builds
    the header ALL_SOURCE does not keep; this drops it again, plus the
    SQL*Plus terminator, so the two can be compared as equals."""
    body = skip_leading_noise(prepare_statement(after_text, "plsql_source"))
    m = CREATE_HEAD_RE.match(body)
    return body[m.end():] if m else body


def drift_note(db_source, last_applied, entry):
    """Whether the object moved since pythia last wrote it.

    The snapshot proves what pythia did; nothing ever proved what anyone else
    did. In the field a comment line was written, read back intact two seconds
    later, and gone eight minutes on with no journal entry in between — and
    the developer went hunting a lying `src` instead of a second writer. The
    journal held both halves of the answer the whole time and nothing compared
    them. Warn, never refuse: editing by hand is legitimate, not noticing is
    not."""
    if not last_applied or not db_source.strip():
        return None
    mine = source_body(last_applied).replace("\r\n", "\n").rstrip()
    now = db_source.replace("\r\n", "\n").rstrip()
    if mine == now:
        return None
    delta = len(now.splitlines()) - len(mine.splitlines())
    how = (f"{abs(delta)} line{'s' if abs(delta) != 1 else ''} "
           + ("more" if delta > 0 else "fewer")) if delta else         "the same line count, different text"
    return (f"This object changed outside pythia since {entry} ({how}). The "
            "diff below is\n  against the database as it stands now, not "
            "against what pythia wrote.")


def last_applied_after(root, obj_type, name):
    """The text pythia last actually wrote to this object, from the journal."""
    for eid in list_journal_entries(root):
        try:
            e = read_journal_entry(root, eid)
        except SystemExit:
            continue
        meta = e["meta"]
        if (meta.get("applied")
                and str(meta.get("object", "")).upper() == name.upper()
                and str(meta.get("type", "")).upper() == obj_type.upper()):
            return eid, e["after"]
    return None, None


def render_restore(obj_type, name, before_text, after_text=""):
    """The statement that puts things back. For an object that did not exist,
    undo means DROP — a genuinely different promise than restoring source, so
    the caller records created=True and the report says it plainly."""
    if before_text.strip():
        return create_prefix(after_text) + before_text.rstrip() + "\n"
    return f"DROP {obj_type} {name}\n"


def write_journal_entry(root, obj_type, name, before, after, meta, now=None,
                        restore=None):
    """Snapshot on disk before anything touches the database. Nothing can turn
    this off: DDL commits itself, so this directory is the only undo there is."""
    import datetime
    now = now or datetime.datetime.now()
    # quoted identifiers can hold '/', ':' or '..' — a directory name must not
    safe = re.sub(r"[^A-Za-z0-9_$#-]", "_", str(name))[:100] or "_"
    eid = (now.strftime("%Y-%m-%dT%H-%M-%S")
           + f"_{safe}_{obj_type.replace(' ', '-')}")
    d = journal_root(root) / eid
    n = 1
    while d.exists():          # apply + restore can land in the same second;
        n += 1                 # overwriting the previous entry would destroy
        d = journal_root(root) / f"{eid}-{n}"   # the only undo there is
    eid = d.name
    d.mkdir(parents=True)
    (d / "before.sql").write_text(before, encoding="utf-8")
    (d / "after.sql").write_text(after, encoding="utf-8")
    (d / "restore.sql").write_text(
        restore if restore is not None
        else render_restore(obj_type, name, before, after), encoding="utf-8")
    full = {"object": name, "type": obj_type, "created": not before.strip(),
            "entry": eid, **meta}
    (d / "meta.json").write_text(json.dumps(full, indent=2, default=str) + "\n",
                                 encoding="utf-8")
    return eid


def read_journal_entry(root, entry_id):
    d = journal_root(root) / entry_id
    if not d.is_dir():
        available = ", ".join(list_journal_entries(root)[:5]) or "none"
        sys.exit(f"No journal entry {entry_id!r}. Recent: {available}. "
                 f"Use: {invocation()} journal list")
    return {"before": (d / "before.sql").read_text(encoding="utf-8"),
            "after": (d / "after.sql").read_text(encoding="utf-8"),
            "restore": (d / "restore.sql").read_text(encoding="utf-8"),
            "meta": json.loads((d / "meta.json").read_text(encoding="utf-8"))}


def list_journal_entries(root):
    d = journal_root(root)
    if not d.is_dir():
        return []
    return sorted((p.name for p in d.iterdir() if p.is_dir()), reverse=True)


def newly_invalid(before_rows, after_rows):
    """(name, type) pairs INVALID now that were not before. The whole point of
    step 5: the agent must not report success while this list is non-empty."""
    return sorted(set(map(tuple, after_rows)) - set(map(tuple, before_rows)))


def render_diff(before, after):
    import difflib
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                      lineterm=""))
    changed = sum(1 for ln in lines
                  if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
    return "\n".join(lines), changed


def json_envelope(command, connection, schema, cols, rows, truncated, **extra):
    payload = {"ok": True, "command": command, "connection": connection,
               "schema": schema, "rows": [dict(zip(cols, r)) for r in rows],
               "truncated": bool(truncated), **extra}
    return json.dumps(payload, default=str, ensure_ascii=False)


# --- database access ---------------------------------------------------------

def authenticating_account(user):
    """The Oracle account that actually authenticates. With proxy
    authentication the connect string is `agent[owner]` and it is the agent
    whose password is checked, whose account locks, and whose name a DBA
    needs — not the schema in front of you."""
    if not user:
        return None
    return str(user).split("[", 1)[0].strip().upper()


def connect_failure_message(exc, conn_name, user=None):
    """A failure to connect should say which entry failed and what to do. For
    the three errors Oracle has already diagnosed precisely, generic advice
    wastes the reader's time, so name the actual next step instead."""
    text = str(exc)
    account = authenticating_account(user)
    who = account or "<the connecting user>"
    head = f"Could not connect using connection {conn_name!r}: {text}"
    if "ORA-28000" in text:
        return (f"{head}\n"
                f"The account {who} is locked. A DBA or superuser unlocks it:\n"
                f"  ALTER USER {who} ACCOUNT UNLOCK;\n"
                "Then find out why, or it locks again — a run of wrong "
                "passwords trips\nFAILED_LOGIN_ATTEMPTS:\n"
                f"  SELECT account_status, lock_date, profile FROM dba_users "
                f"WHERE username = '{who}';")
    if "ORA-28001" in text or "ORA-28002" in text:
        return (f"{head}\n"
                f"The password for {who} has expired. A DBA sets a new one:\n"
                f"  ALTER USER {who} IDENTIFIED BY \"<new password>\";\n"
                "Update it in connections.json in the same breath. To stop the "
                "clock for a\nservice account, put it on a profile with "
                "PASSWORD_LIFE_TIME UNLIMITED.")
    if "ORA-01017" in text:
        return (f"{head}\n"
                f"Wrong username or password for {who}. Check the entry in "
                "connections.json —\nand check it before retrying: repeated "
                "attempts trip FAILED_LOGIN_ATTEMPTS and\nlock the account, "
                "which needs a DBA to undo.")
    return (f"{head}\n"
            "Check host/port/service_name, the credentials, and that the database "
            "is reachable from here. Use --conn NAME to try a different entry.")


def open_pool(c):
    import oracledb
    if not c.get("user") or not c.get("password"):
        sys.exit(f"Connection is missing 'user'/'password' — fill in {CONFIG_NAME}.")
    if not (c.get("dsn") or c.get("host")):
        sys.exit(f"Connection needs 'dsn' or 'host' — fill in {CONFIG_NAME}.")
    dsn = c.get("dsn") or oracledb.makedsn(
        c["host"], int(c.get("port", 1521)),
        service_name=c.get("service_name") or None, sid=c.get("sid") or None)
    # ponytail: a pool only helps within one invocation; cross-invocation reuse
    # needs the MCP backend (persistent session) or a future resident mode.
    return oracledb.create_pool(user=c["user"], password=c["password"], dsn=dsn,
                                min=1, max=2)


def session_should_be_readonly(command, action=""):
    """Read commands get SET TRANSACTION READ ONLY as a second line of
    defence. The write path must not: DML under a read-only transaction dies
    with ORA-01456, and its defences are the classifier, the policy gate, the
    token and the snapshot — not a transaction attribute."""
    if command in ("apply", "deploy", "explain"):
        # explain writes PLAN_TABLE rows (rolled back); it runs no statement
        return False
    if command == "journal" and action == "restore":
        return False
    return True


def acquire(pool, readonly=True):
    conn = pool.acquire()
    if readonly:
        with conn.cursor() as cur:                    # defence 2: Oracle itself rejects
            cur.execute("set transaction read only")  # DML/DDL in this transaction.
    return conn                                       # NOT inherited by autonomous
    #                                                   transactions inside called PL/SQL.


def cell(v):
    if v is None:
        return ""
    if hasattr(v, "read"):  # LOB
        v = v.read()
    return str(v).replace("\t", "    ")


def run_query(conn, sql, binds=None):
    with conn.cursor() as cur:
        cur.execute(sql, binds or {})
        return [d[0] for d in cur.description], cur.fetchall()


def fetch_n(limit):
    # bind one row beyond the limit so truncation is detected, never guessed
    return limit + 1 if limit else INT_MAX


# --- output ------------------------------------------------------------------

def emit_table(ns, cols, rows, truncated):
    if ns.json:
        print(json_envelope(ns.command, ns.conn_name, ns.schema, cols, rows, truncated))
        return
    print("\t".join(cols))
    for r in rows:
        print("\t".join(cell(v) for v in r))
    if truncated:
        print(f"-- truncated at {len(rows)} rows (raise --limit, or --limit 0 for no cap)")
    print(f"-- {len(rows)} rows", file=sys.stderr)


def emit_source(ns, rows, truncated, total):
    if ns.json:
        clean = [(u, ln, str(t).rstrip("\n")) for u, ln, t in rows]
        print(json_envelope(ns.command, ns.conn_name, ns.schema,
                            ("TYPE", "LINE", "TEXT"), clean, truncated, total_lines=total))
        return
    sys.stdout.write(format_source(rows, ns.raw))
    if truncated:
        print(f"-- truncated: showing {len(rows)} of {total} source lines "
              f"(use --offset {ns.offset + len(rows)} to continue, or --max-lines 0 for all)")


# --- subcommands (SQL kept verbatim from the field-proven v1 tool) -----------

def cmd_check(conn, schema, ns):
    en = getattr(ns, "color", False)
    if en:
        sys.stdout.write(banner(en))
    cols, rows = run_query(conn, """
        select user connected_as, :s owner_used,
               sys_context('userenv','db_name') db,
               (select count(*) from all_objects   where owner = :s) objects,
               (select count(*) from all_views     where owner = :s) views,
               (select count(*) from all_types     where owner = :s) types,
               (select count(*) from all_indexes   where owner = :s) indexes,
               (select count(*) from all_triggers  where owner = :s) triggers
        from dual""", {"s": schema})
    emit_table(ns, cols, rows, False)
    current = session_schema(conn)
    if current and current != str(schema).upper():
        print("\n" + paint(
            f"! Session schema is {current}, but this connection is configured "
            f"for {schema}: unqualified writes would land in {current} while "
            f"pythia reads {schema}. apply refuses until connections.json says "
            f"\"schema\": \"{current}\".", "yellow", color_enabled(sys.stderr)),
            file=sys.stderr)
    warn = privilege_warning(conn, schema, ns.conn_user)
    if warn:
        print("\n" + paint(warn, "yellow", color_enabled(sys.stderr)),
              file=sys.stderr)
    report_drift(drift_summary(conn, schema, ns))


def drift_summary(conn, schema, ns):
    """One line, only when objects pythia knows have moved since it last saw
    them. Uses LAST_DDL_TIME — a single query for every journaled object,
    instead of re-reading every source.
    ponytail: LAST_DDL_TIME also ticks on a bare recompile, so this is a
    'go look' signal; `history` and the src/impact hash comparison are the
    precise ones."""
    root = getattr(ns, "project_root", None)
    if root is None:
        return None
    try:
        known = journaled_objects(root)
        if not known:
            return None
        names = sorted(known)[:200]        # one query, bounded
        binds = {"s": schema}
        placeholders = []
        for i, n in enumerate(names):
            binds[f"n{i}"] = n
            placeholders.append(f":n{i}")
        _, rows = run_query(
            conn, "select object_name, to_char(last_ddl_time,"
            "'yyyy-mm-dd\"T\"hh24-mi-ss') from all_objects "
            f"where owner = :s and object_name in ({','.join(placeholders)})",
            binds)
        moved = [r[0] for r in rows if str(r[1]) > known.get(str(r[0]), "")]
        if not moved:
            return None
        shown = ", ".join(moved[:3]) + ("…" if len(moved) > 3 else "")
        return (f"! {len(moved)} object(s) changed since pythia last saw them: "
                f"{shown}\n  Versions and restores: {invocation()} history "
                f"<NAME>")
    except Exception:                      # noqa: BLE001
        return None


def cmd_ls(conn, schema, ns):
    cols, rows = run_query(conn, """
        select object_name, object_type, status, to_char(last_ddl_time,'yyyy-mm-dd') last_ddl
        from all_objects where owner = :s and object_name like upper(:p)
        order by object_type, object_name fetch first :n rows only""",
        {"s": schema, "p": ns.pattern, "n": fetch_n(ns.limit)})
    rows, truncated = clip(rows, ns.limit)
    emit_table(ns, cols, rows, truncated)


def cmd_src(conn, schema, ns):
    _, rows = run_query(conn, load_query("source.sql"), {"s": schema, "n": ns.name})
    rows = [(t, ln, cell(x)) for t, ln, x in rows]
    rows = filter_units(rows, ns.body, ns.spec)
    if not rows:
        sys.exit(f"No source found for {ns.name!r} in schema {schema}.")
    total = len(rows)
    drift = auto_snapshot(conn, schema, ns.name, ns)
    shown, truncated = clip(rows, ns.max_lines, ns.offset)
    emit_source(ns, shown, truncated, total)
    report_drift(drift)


def cmd_args(conn, schema, ns):
    cols, rows = run_query(conn, """
        select position, argument_name, in_out, data_type,
               type_name, type_subname, data_level, defaulted
        from all_arguments
        where owner = :s and object_name = upper(:n) and argument_name is not null
        order by position""", {"s": schema, "n": ns.name})
    emit_table(ns, cols, rows, False)


def cmd_ddl(conn, schema, ns):
    with conn.cursor() as cur:  # strip STORAGE/SEGMENT noise before it burns context
        cur.execute("begin dbms_metadata.set_transform_param("
                    "dbms_metadata.session_transform,'STORAGE',false); "
                    "dbms_metadata.set_transform_param("
                    "dbms_metadata.session_transform,'SEGMENT_ATTRIBUTES',false); end;")
    _, rows = run_query(conn, "select dbms_metadata.get_ddl(upper(:t), upper(:n), :s) from dual",
                        {"t": ns.type, "n": ns.name, "s": schema})
    lines = cell(rows[0][0]).split("\n") if rows else []
    shown, truncated = clip(lines, ns.max_lines, ns.offset)
    if ns.json:
        print(json_envelope(ns.command, ns.conn_name, ns.schema, ("DDL",),
                            [("\n".join(shown),)], truncated, total_lines=len(lines)))
        return
    print("\n".join(shown))
    if truncated:
        print(f"-- truncated: showing {len(shown)} of {len(lines)} lines "
              f"(use --offset {ns.offset + len(shown)} to continue, or --max-lines 0 for all)")


def cmd_cols(conn, schema, ns):
    cols, rows = run_query(conn, """
        select column_id, column_name, data_type, data_length, data_precision,
               data_scale, nullable, char_used, data_default
        from all_tab_columns where owner = :s and table_name = upper(:n)
        order by column_id""", {"s": schema, "n": ns.name})
    emit_table(ns, cols, rows, False)


def cmd_grep(conn, schema, ns):
    cols, rows = run_query(conn, """
        select name, type, line, trim(text) text from all_source
        where owner = :s and upper(text) like upper('%' || :p || '%')
        order by name, line fetch first :n rows only""",
        {"s": schema, "p": ns.pattern, "n": fetch_n(ns.limit)})
    rows, truncated = clip(rows, ns.limit)
    emit_table(ns, cols, rows, truncated)


def cmd_sql(conn, schema, ns):
    stmt = " ".join(ns.statement).strip().rstrip(";")
    if not is_readonly_sql(stmt):
        sys.exit("Only SELECT/WITH statements are allowed; pythia is read-only.")
    cols, rows = run_query(conn, stmt)
    rows, truncated = clip(rows, ns.limit)
    if ns.raw and not ns.json:
        for r in rows:
            sys.stdout.write(cell(r[0]).rstrip("\n") + "\n")
        if truncated:
            print(f"-- truncated at {len(rows)} rows (raise --limit, or --limit 0 for no cap)")
        return
    emit_table(ns, cols, rows, truncated)


def cmd_invalid(conn, schema, ns):
    cols, rows = run_query(conn, load_query("invalid-objects.sql"), {"s": schema})
    rows, truncated = clip(rows, ns.limit)
    emit_table(ns, cols, rows, truncated)


def cmd_errors(conn, schema, ns):
    cols, rows = run_query(conn, load_query("compile-errors.sql"),
                           {"s": schema, "n": ns.name})
    rows, truncated = clip(rows, ns.limit)
    if ns.json:
        print(json_envelope(ns.command, ns.conn_name, ns.schema, cols, rows, truncated))
        return
    if not rows:
        target = ns.name or f"schema {schema}"
        print(f"-- no compilation errors for {target}")
        return
    sys.stdout.write(format_errors(rows))
    if truncated:
        print(f"-- truncated at {len(rows)} rows (raise --limit, or --limit 0 for no cap)")


def cmd_deps(conn, schema, ns):
    cols, rows = run_query(conn, load_query("dependencies.sql"),
                           {"s": schema, "n": ns.name, "depth": ns.depth,
                            "with_sys": 1 if ns.with_sys else 0})
    rows, truncated = clip(rows, ns.limit)
    if ns.json:
        print(json_envelope(ns.command, ns.conn_name, ns.schema, cols, rows, truncated))
        return
    if not rows:
        print(f"-- {ns.name.upper()} depends on nothing (or does not exist in {schema})")
        return
    sys.stdout.write(render_tree(rows, f"{schema}.{ns.name.upper()}"))
    if truncated:
        print(f"-- truncated at {len(rows)} rows (raise --limit, or --limit 0 for no cap)")


def cmd_impact(conn, schema, ns):
    cols, rows = run_query(conn, load_query("impact.sql"),
                           {"s": schema, "n": ns.name, "depth": ns.depth})
    # impact is mandatory before any change, so it is the surest "about to
    # touch this" signal there is — the best possible place to capture a
    # before-state the developer never had to ask for
    drift = auto_snapshot(conn, schema, ns.name, ns)
    shown, truncated = clip(rows, ns.limit)
    if ns.json:
        print(json_envelope(ns.command, ns.conn_name, ns.schema, cols, shown, truncated,
                            summary=impact_summary(rows)))
        report_drift(drift)
        return
    if not rows:
        print(f"-- nothing depends on {ns.name.upper()} "
              f"(within {schema}, depth {ns.depth})")
        report_drift(drift)
        return
    sys.stdout.write(render_tree(shown, f"{schema}.{ns.name.upper()}"))
    if truncated:
        print(f"-- truncated at {len(shown)} rows (raise --limit, or --limit 0 for no cap)")
    print(impact_summary(rows))
    report_drift(drift)


def cmd_similar(conn, schema, ns):
    cols, rows = run_query(conn, load_query("similar-candidates.sql"), {"s": schema})
    ranked = rank_similar(ns.name, rows)
    shown, truncated = clip(ranked, ns.limit)
    if not shown and not ns.json:
        print(f"-- nothing in {schema} shares a name token with {ns.name.upper()}")
        return
    emit_table(ns, [*cols, "MATCHED_TOKENS"], shown, truncated)


def cmd_plscope(conn, schema, ns):
    cols, rows = run_query(conn, load_query("plscope-usages.sql"),
                           {"s": schema, "n": ns.name})
    if not rows:
        _, probe = run_query(conn, load_query("plscope-enabled.sql"), {"s": schema})
        sys.exit(plscope_message(ns.name, bool(probe)))
    stmt_cols, stmt_rows = (), []
    if any(str(r[3]).upper() == "TABLE" for r in rows):   # TYPE column
        stmt_cols, stmt_rows = run_query(conn, load_query("plscope-statements.sql"),
                                         {"s": schema, "n": ns.name})
    shown, truncated = clip(rows, ns.limit)
    if ns.json:
        print(json_envelope(ns.command, ns.conn_name, ns.schema, cols, shown, truncated,
                            statements=[dict(zip(stmt_cols, r)) for r in stmt_rows]))
        return
    emit_table(ns, cols, shown, truncated)
    if stmt_rows:
        print("\n-- SQL statements touching this table")
        emit_table(ns, stmt_cols, *clip(stmt_rows, ns.limit))


AGENT_USER_SQL = """\
-- Least-privilege agent credential for schema {owner} — run as a DBA.
-- Proxy authentication: the agent logs in with its OWN password but works
-- inside {owner}; it never learns the owner password, owns nothing, and
-- revocation is one statement. Deliberately absent: DBA, RESOURCE, ANY
-- privileges, utility grants — the agent needs none of them to develop
-- PL/SQL, and every extra grant widens the blast radius.

{create_line}
GRANT CREATE SESSION TO {agent};
ALTER USER {owner} GRANT CONNECT THROUGH {agent};

-- Cut the agent off later (owner untouched):
--   ALTER USER {owner} REVOKE CONNECT THROUGH {agent};
-- Fresh owner schema instead? See examples/agent-user-setup.example.sql.
"""


CONVENTIONS_TEMPLATE = """{
  "naming": {
    "TABLE": "^T_[A-Z0-9_]+$",
    "PROCEDURE": "^P_[A-Z0-9_]+$",
    "FUNCTION": "^F_[A-Z0-9_]+$",
    "PACKAGE": "^PKG_[A-Z0-9_]+$",
    "SEQUENCE": "^S_[A-Z0-9_]+$",
    "TRIGGER": "^TRG_[A-Z0-9_]+$"
  },
  "lint": {
    "rules": {},
    "require_comments": "warning",
    "required_columns": [],
    "forbid": []
  }
}
"""

CONVENTIONS_PROSE_TEMPLATE = """# Project conventions

Rules for anyone — human or agent — writing PL/SQL in this schema. Agents are
told to read this before writing, and it outranks the generic patterns the
skill pack ships with.

`conventions.json` next to this file holds the naming patterns as regexes,
and under "lint" the rules a linter can check: per-rule severities (`pythia
lint --json` lists the rule ids), mandatory comments, required columns, a
parameter prefix, forbidden patterns. Every `pythia lint` and apply preview
checks both. Keep the two files in step: this file explains, that file
enforces.

## Naming

Replace the patterns in `conventions.json` with yours, then describe them
here so the reasoning survives. `pythia similar <A_TYPICAL_NAME>` shows what
the schema already does — copy that rather than inventing a scheme.

| Kind | Rule | Example |
|---|---|---|
| Table | | |
| Procedure | | |
| Function | | |

## Rules that carry a cost when broken

State the consequence, not just the rule — a rule with a named cost gets
followed. For example: which parameter prefixes the calling layer depends on,
which column every query must filter by, where a transaction may commit.

| Rule | Cost of breaking it |
|---|---|
| | |

## Known exceptions

Objects that break the pattern on purpose, and why renaming them is worse
than the warning they produce on every apply.
"""


SCAN_SHARE = 0.9        # a token set must cover this much to count as the rule
SCAN_MAX_ALTERNATIVES = 12


def _dominant(tokens_at_position, total):
    """The smallest set of tokens covering SCAN_SHARE of the names, or None.

    A set only counts as a rule when its tokens repeat. As many distinct
    tokens as there are names is a list of names, not a convention — and
    writing that down produces a pattern that fails on the next object
    anyone adds.
    """
    limit = min(SCAN_MAX_ALTERNATIVES, max(1, total // 2))
    counts = {}
    for tok in tokens_at_position:
        counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    picked, covered = [], 0
    for tok, n in ranked:
        if len(picked) >= limit:
            return None
        picked.append(tok)
        covered += n
        if covered >= total * SCAN_SHARE:
            return picked
    return None


def propose_pattern(names, min_names=3):
    """Read the shape off real names: a dominant first token, and a dominant
    last token when there is one. Returns None when the names share no
    structure — configuring nothing beats configuring a rule that means
    nothing."""
    names = [str(n).upper() for n in names if n]
    if len(names) < min_names:
        return None
    split = [n.split("_") for n in names]
    if sum(1 for p in split if len(p) >= 2) < len(names) * SCAN_SHARE:
        return None                       # mostly single-word names: no shape
    heads = _dominant([p[0] for p in split], len(names))
    if not heads:
        return None
    head = heads[0] if len(heads) == 1 else "(" + "|".join(sorted(heads)) + ")"
    # the suffixed form needs a third token to sit in; without one for most
    # names, an alternation at the end would exclude the short ones
    if sum(1 for p in split if len(p) >= 3) >= len(names) * SCAN_SHARE:
        tails = _dominant([p[-1] for p in split], len(names))
        if tails:
            tail = (tails[0] if len(tails) == 1
                    else "(" + "|".join(sorted(tails)) + ")")
            return f"^{head}_[A-Z0-9_]+_{tail}$"
    return f"^{head}_[A-Z0-9_]+$"


def scan_conventions(objects):
    """Propose a naming block from the schema itself. The tool tokenizes the
    names so the agent never has to read thousands of them into context; the
    developer's own document decides what is kept."""
    by_type = {}
    for otype, name in objects:
        by_type.setdefault(str(otype).upper(), []).append(name)
    naming = {}
    for otype, names in sorted(by_type.items()):
        pattern = propose_pattern(names)
        if pattern:
            naming[otype] = pattern
    return {"naming": naming}


def pattern_coverage(conv, objects):
    """How well each configured pattern describes the names already in the
    schema. objects: (object_type, object_name).

    A pattern derived from a document, or from reading a few examples, is a
    guess. Measuring it against every real name turns the guess into a number
    and names the exceptions — which is the difference between conventions
    that hold and conventions that produce a warning on every apply.
    """
    patterns = (conv or {}).get("naming") or {}
    out = {}
    for otype, pattern in patterns.items():
        rx = re.compile(pattern)
        names = sorted(n for t, n in objects if str(t).upper() == otype.upper())
        misses = [n for n in names if not rx.match(str(n))]
        out[otype] = {"pattern": pattern, "total": len(names),
                      "matched": len(names) - len(misses), "misses": misses}
    return out


def coverage_verdict(matched, total):
    """Read the numbers so nobody has to. A low rate means the pattern is
    wrong far more often than it means the schema is."""
    if total == 0:
        return "nothing of that type in this schema — untested rule"
    if matched == total:
        return "every name matches"
    pct = round(100 * matched / total)
    if pct >= 90:
        return f"{pct}% match — the rest are worth listing as exceptions"
    return (f"only {pct}% match — the derived pattern is probably wrong; "
            "widen it or split it before writing it down")


def scaffold_conventions(root):
    """Write the conventions pair, skipping anything that already exists.
    Returns the paths actually created.

    The docs used to say "copy examples/conventions.example.json", which is
    no help to anyone who installed the wheel: there is no examples directory
    there. The tool carries the templates instead.
    """
    d = pathlib.Path(root) / CONFIG_DIR
    made = []
    for name, body in (("conventions.json", CONVENTIONS_TEMPLATE),
                       ("conventions.md", CONVENTIONS_PROSE_TEMPLATE)):
        path = d / name
        if path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        made.append(path)
    return made


def load_conventions(root):
    """Project house style from .pythia/conventions.json — the machine half of
    the customization surface (the prose half is conventions.md, for agents).
    Config is a trust boundary: unknown keys and broken regexes are refused
    with the fix, not skipped."""
    path = pathlib.Path(root) / CONFIG_DIR / "conventions.json"
    if not path.is_file():
        return None
    try:
        conv = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        sys.exit(f"Cannot parse {path}: {e}")
    unknown = set(conv) - {"naming", "lint"}
    if unknown:
        sys.exit(f"Unknown key {sorted(unknown)[0]!r} in {path} — "
                 "supported: \"naming\", \"lint\".")
    lint_config(conv)                     # refuses a bad lint block with the fix
    for otype, pattern in conv.get("naming", {}).items():
        try:
            re.compile(pattern)
        except re.error as e:
            sys.exit(f"Bad regex for {otype!r} in {path}: {e}")
    return conv


def naming_violation(otype, name, conv):
    """The warning line for a name outside the project's pattern, or None.
    Style warns; only policy blocks."""
    pattern = ((conv or {}).get("naming") or {}).get(otype)
    if pattern and not re.match(pattern, name):
        return (f"naming: {name} does not match this project's {otype} "
                f"pattern {pattern} — see .pythia/conventions.md")
    return None


# Which main-namespace occupants may legally coexist with each object type.
# Spec and body pair up; triggers have a namespace of their own (None = nothing
# in the main namespace can block them).
NAMESPACE_COEXIST = {
    "PROCEDURE": {"PROCEDURE"},
    "FUNCTION": {"FUNCTION"},
    "PACKAGE": {"PACKAGE", "PACKAGE BODY"},
    "PACKAGE BODY": {"PACKAGE", "PACKAGE BODY"},
    "TYPE": {"TYPE", "TYPE BODY"},
    "TYPE BODY": {"TYPE", "TYPE BODY"},
    "VIEW": {"VIEW"},
    "TRIGGER": None,
}


def name_conflicts(otype, occupants):
    """Occupant types that CREATE OR REPLACE <otype> cannot replace — the
    ORA-00955 the preview must predict instead of discovering at apply time."""
    allowed = NAMESPACE_COEXIST.get(otype)
    if allowed is None:
        return []
    return sorted(set(occupants) - allowed)


def privilege_warning(conn, schema, conn_user):
    """One line, only when true. The policy file is an application-side fence;
    Oracle grants are the only layer that cannot be walked around, so say when
    this session is running with more power than the task needs."""
    _, rows = run_query(conn, load_query("session-privileges.sql"))
    dangerous = [r[0] for r in rows]
    with conn.cursor() as cur:
        cur.execute("select sys_context('userenv','proxy_user') from dual")
        prow = cur.fetchall()
    proxy = prow[0][0] if prow and prow[0] and prow[0][0] else None
    if proxy:
        # sanctioned least-privilege entrance; any excess power is the
        # OWNER's, inherited by every proxied session — say that
        if not dangerous:
            return None
        return (f"! Proxy session ({proxy} → {schema}), but the owner holds "
                f"{', '.join(dangerous[:3])}" + ("…" if len(dangerous) > 3 else "")
                + ":\n  every proxied session inherits this — trim the "
                "owner's grants; pythia policy explains what is at stake.")
    owner = bool(conn_user) and conn_user.upper() == schema
    if not owner and not dangerous:
        return None
    what = ("the schema owner" if owner else
            f"a user holding {', '.join(dangerous[:3])}"
            + ("…" if len(dangerous) > 3 else ""))
    return (f"! Connected as {what}: this account can do far more than apply "
            "PL/SQL.\n  A least-privilege account is safer — pythia policy "
            "explains what is at stake.")


def session_schema(conn):
    """Where an unqualified name lands for this session, or None if the
    database did not say (it always does on Oracle; test doubles may not)."""
    _, rows = run_query(conn, load_query("current-schema.sql"), {})
    return str(rows[0][0]).upper() if rows and rows[0] and rows[0][0] else None


def refuse_schema_mismatch(conn, schema, ns):
    """An unqualified CREATE lands in the session's current schema, while
    every snapshot, impact and verify query here asks the configured one. If
    those differ — a proxy into another user, a stale `schema` in
    connections.json — the write would land in one place and be verified in
    another, and "Applied, compiled clean" would be about the wrong object.
    Found live: PYTHIA_AGENT[PYTHIA_DEV] with schema still set to PYTHIA."""
    current = session_schema(conn)
    if current and current != str(schema).upper():
        sys.exit(f"Refused: this session's schema is {current!r}, but connection "
                 f"{getattr(ns, 'conn_name', None) or '?'!r} is configured for "
                 f"{schema!r}.\nAn unqualified statement would land in {current!r} "
                 f"while pythia snapshots and verifies {schema!r}.\nSet "
                 f"\"schema\": \"{current}\" for this connection in "
                 f".pythia/connections.json (or connect as {schema!r}), then "
                 "preview again.")


def run_apply(conn, schema, ns, file_text, origin=None):
    """The six steps: SNAPSHOT, IMPACT, PREVIEW, APPLY, VERIFY, REPORT.
    Returns the exit code. Refusals raise SystemExit (exit 1)."""
    group = classify(file_text)
    if group == "anonymous":
        sys.exit("Anonymous PL/SQL blocks are refused: a BEGIN...END block can "
                 "EXECUTE IMMEDIATE anything, so no policy group honestly fits.\n"
                 "Wrap the logic in a named procedure and apply that instead.")
    if group is None:
        sys.exit("Cannot classify this statement, so it is refused rather than "
                 "guessed at. pythia apply takes one CREATE OR REPLACE / DML / "
                 "DDL / GRANT statement per file.")
    action = load_policy(ns.project_root)[group][0]
    # lint first: it is pure, and the autopilot gate needs its verdict
    # before the policy gate can be passed on the lease's authority
    try:
        findings = lint_findings(file_text, group,
                                 load_conventions(ns.project_root))
        if group == "plsql_source":       # the preview's own naming line says it
            findings = [f for f in findings if f["rule"] != "naming"]
    except SystemExit:
        raise                             # a broken conventions.json is refused
    except Exception:                     # noqa: BLE001 — a lint bug never blocks
        findings = []
    lint_errors = [f for f in findings if f["severity"] == "error"]
    if getattr(ns, "preview", False):
        auto_ok, lease, auto_why = False, None, None
    else:
        auto_ok, lease, auto_why = autopilot_check(
            ns.project_root, getattr(ns, "conn_name", ""), group, file_text)
    if auto_ok and lint_errors:
        auto_ok, auto_why = False, (f"{len(lint_errors)} lint error(s) — "
                                    "autopilot does not land them; fix the "
                                    "file, or the developer approves it as is")
    if action == "deny" and (auto_ok or lease_names_group(
            ns.project_root, getattr(ns, "conn_name", ""), group)):
        # the lease card named this group: previews and per-statement
        # approvals pass the deny, auto-landing still needs auto_ok
        action = "confirm"
    if action == "deny":
        extra = ("no snapshot can undo it after commit"
                 if group in ("data_dml", "structural", "grants")
                 else "policy forbids it")
        note = ("\nRevalidation narrows that risk without removing it: the "
                "preview shows the rows and the count, apply refuses if that "
                "set moved, and the write is rolled back if the statement "
                "touches a different number of rows. What nothing here can do "
                "is undo a committed DELETE."
                if group == "data_dml" else "")
        sys.exit(f"Refused: {group} is set to deny — {extra}.{note}\n"
                 f"To allow it once you have weighed that: "
                 f"{invocation()} policy set {group} confirm")
    stmt = prepare_statement(file_text, group)

    if group == "session":
        # Nothing persistent changes; and the setting dies with this process's
        # connection, so say so instead of pretending it did something lasting.
        with conn.cursor() as cur:
            cur.execute(stmt)
        print("Session parameter set — note it lasts only for this "
              "invocation's connection, which is now over.")
        return 0

    edition_note = None
    if group == "plsql_source":
        otype, name, file_schema = parse_object(file_text)
        if file_schema and file_schema.upper() != schema:
            sys.exit(f"The file names schema {file_schema.upper()!r} but this "
                     f"connection targets {schema!r}. Refused: applying across "
                     "schemas hides which database object actually changes.\n"
                     f"Use --conn to select the {file_schema.upper()!r} connection.")
        _, occ_rows = run_query(conn, load_query("name-occupants.sql"),
                                {"s": schema, "n": name})
        blockers = name_conflicts(otype, [r[0] for r in occ_rows])
        stmt, edition_note = reconcile_editionable(
            stmt, next((cell(r[1]) for r in occ_rows
                        if str(r[0]).upper() == otype.upper()), None))
        if blockers:
            sys.exit(f"{name} already exists as {', '.join(blockers)} in {schema} "
                     "— CREATE OR REPLACE cannot change an object's type "
                     "(ORA-00955 would follow).\n"
                     "Changing the type means DROP first — structural, and the "
                     "policy on that group applies:\n"
                     f"  {invocation()} policy set structural confirm   "
                     "(only if you accept losing the old object)")
    elif group == "comments":
        ckind, cschema, ctable, ccolumn = parse_comment_target(stmt)
        if cschema and cschema.upper() != schema:
            sys.exit(f"The statement names schema {cschema.upper()!r} but this "
                     f"connection targets {schema!r}. Refused: applying across "
                     "schemas hides which database object actually changes.\n"
                     f"Use --conn to select the {cschema.upper()!r} connection.")
        otype = "COMMENT"
        name = f"{ctable}.{ccolumn}" if ccolumn else ctable
    else:
        # confirm-mode DML/DDL/grants: no object identity, no snapshot — the
        # journal records the statement itself so at least *what ran* is kept.
        otype, name = group.upper(), "STATEMENT"

    refuse_schema_mismatch(conn, schema, ns)   # the write must land where it is verified

    # 1. SNAPSHOT — before anything else, unconditionally.
    db_source = ""
    row_set = None
    if group == "plsql_source":
        _, rows = run_query(conn, load_query("object-source.sql"),
                            {"s": schema, "n": name, "t": otype})
        db_source = "".join(cell(r[0]) for r in rows)
    elif group == "comments":
        # the comment as it stands IS the snapshot: one dictionary read, and
        # the undo is a COMMENT statement putting it back
        _, rows = run_query(conn, load_query("comment-current.sql"),
                            {"s": schema, "t": ctable, "c": ccolumn})
        db_source = cell(rows[0][0]) if rows and rows[0][0] is not None else ""
    elif group == "data_dml":
        # the row set IS the snapshot here: nothing can undo a committed DML,
        # so what the developer approves is the set, and the token carries it
        probe = dml_probe(stmt)
        if probe:
            row_set = measure_row_set(conn, probe[1], probe[2])
    token = apply_token(otype, name, file_text,
                        db_source + fingerprint_text(row_set))
    cap = int((lease or {}).get("max_rows") or LEASE_DML_MAX_ROWS)
    if auto_ok and row_set and row_set["count"] > cap:
        auto_ok, auto_why = False, (f"{row_set['count']} rows — more than the "
                                    f"lease's {cap}; a set this size is shown "
                                    "to the developer first")
    if ns.yes and not human_at_the_keyboard():
        sys.exit(HEADLESS_YES_MSG)
    if ns.confirm and ns.confirm != token:
        sys.exit(token_mismatch_message(ns, row_set))
    # The token proves the content did not move; the grant proves a human
    # approved it. --yes is itself that human act, at a real console; an
    # autopilot lease is the same act, given once for a scope it names.
    grant = None
    via = None
    if ns.confirm == token:
        grant = read_grant(ns.project_root, token)
        status = grant_status(grant, ns.conn_name)
        if status == "ok" and (grant or {}).get("batch") and \
                (grant or {}).get("batch") != getattr(ns, "batch", None):
            # approved as statement N of a release, not on its own
            if not auto_ok:
                sys.exit(f"That approval belongs to deploy {grant['batch']}: it "
                         "covers this statement only as part of that release, "
                         "in its order.\nRun the release:\n  "
                         f"{invocation()} deploy <files> --confirm {grant['batch']}")
            grant, via = None, "autopilot"
        elif status == "ok":
            via = "grant"
        elif auto_ok:
            grant, via = None, "autopilot"
        else:
            sys.exit(grant_refusal(status, token, grant or {},
                                   ns.conn_name, ns.file))
    elif auto_ok:
        via = "autopilot"
    if ns.yes:
        via = "yes"
    confirmed = via is not None
    hurt = destructive_reason(file_text, group)

    _, inv_rows = run_query(conn, load_query("invalid-objects.sql"), {"s": schema})
    invalid_before = [(r[0], r[1]) for r in inv_rows]
    meta = {"schema": schema, "connection": ns.conn_name, "group": group,
            "token": token, "applied": False,
            "confirmed_via": via or "preview",
            "autopilot": (lease or {}).get("token") if via == "autopilot" else None,
            "lint": findings,
            "destructive": hurt,
            "row_set": row_set,
            "grant_minted_at": (grant or {}).get("minted_at"),
            "tty": human_at_the_keyboard(),
            "invalid_before": invalid_before, **(origin or {})}
    entry = write_journal_entry(
        ns.project_root, otype, name, db_source,
        stmt if edition_note else file_text, meta,
        restore=(render_comment(ckind, ctable, ccolumn, db_source)
                 if group == "comments" else None))
    # "created" is about an OBJECT that did not exist. A DML/DDL statement has
    # no object identity, so it is never "new" — and saying so would promise a
    # DROP-shaped undo that does not exist for those groups.
    created = not db_source.strip() and group == "plsql_source"

    drift = None
    if group == "plsql_source" and db_source.strip():
        eid, last = last_applied_after(ns.project_root, otype, name)
        drift = drift_note(db_source, last, eid)

    # 2. IMPACT
    summary = ""
    if group == "plsql_source":
        _, dep_rows = run_query(conn, load_query("impact.sql"),
                                {"s": schema, "n": name, "depth": ns.depth})
        summary = impact_summary(dep_rows)

    # 3. PREVIEW — diff like against like: ALL_SOURCE never stores the
    # CREATE OR REPLACE header, so prepend it before comparing, or an
    # unchanged object would show a phantom two-line change forever. The
    # editionable keyword is part of that header, or a NONEDITIONABLE file
    # shows its own first line as a change that is not one.
    if group == "comments":
        base = render_comment(ckind, ctable, ccolumn, db_source) \
            if db_source else ""
    else:
        base = (create_prefix(stmt) + db_source) if db_source.strip() else ""
    diff_text, changed = render_diff(base, stmt)
    warn = privilege_warning(conn, schema, ns.conn_user)
    style = (naming_violation(otype, name, load_conventions(ns.project_root))
             if group == "plsql_source" else None)
    if ns.json:
        print(json.dumps({"ok": True, "object": name, "type": otype,
                          "created": created, "changed_lines": changed,
                          "summary": summary, "warning": warn,
                          "row_set": row_set,
                          "naming_warning": style,
                          "editionable_note": edition_note, "drift": drift,
                          "lint": findings, "destructive": hurt,
                          "autopilot": ({"covered": auto_ok, "why": auto_why,
                                         "lease": (lease or {}).get("token")}
                                        if lease else None),
                          "token": token,
                          "journal": entry, "will_apply": confirmed,
                          "confirmed_via": via}))
    else:
        en = getattr(ns, "color", False)
        if group != "plsql_source":
            print(f"\n  {paint(group, 'bold', en)} statement on {schema}")
        else:
            if created:
                head = "new object"
            elif changed == 0:
                head = "no source change (recompile)"
            else:
                head = f"{changed} lines changed"
            print(f"\n  {paint(f'{name} ({otype})', 'bold', en)} in {schema} "
                  f"— {head}")
        if summary:
            print(f"  {summary.lstrip('- ')}")
        if row_set is not None:
            print()
            for ln in render_row_set(row_set, en):
                print(ln)
        elif group == "data_dml":
            print("\n  INSERT — no rows exist beforehand, so there is no row "
                  "set to revalidate.")
        if drift:
            print(f"\n  {paint('! ' + drift, 'yellow', en)}")
        if edition_note:
            print(f"\n  {paint('! ' + edition_note, 'yellow', en)}")
        if warn:
            print(f"\n  {paint(warn, 'yellow', en)}")
        if style:
            print(f"\n  {paint('! ' + style, 'yellow', en)}")
        if hurt:
            print(f"\n  {paint('! DESTRUCTIVE: ' + hurt + '.', 'red', en)}")
        if findings:
            print(f"\n  lint: {lint_summary(findings)}")
            for ln in render_findings(findings, en=en, limit=8):
                print(f"    {ln}")
        if lease and auto_ok:
            print("\n  " + paint(f"autopilot: applying under lease "
                                 f"{lease.get('token')} (write "
                                 f"{int(lease.get('writes', 0)) + 1} of "
                                 f"{lease.get('max_writes')})", "cyan", en))
        elif lease and auto_why:
            print("\n  " + paint(f"! autopilot: not covered — {auto_why}",
                                 "yellow", en))
        if diff_text:
            print()
            for ln in diff_text.splitlines():
                print(f"  {paint_diff_line(ln, en)}")
        print(paint(f"\n  Snapshot saved: {journal_root(ns.project_root) / entry}",
                    "dim", en))
        if not confirmed and group == "plsql_source":
            # only this group has a restore.sql worth running: the others get a
            # generated DROP that nothing would accept
            print(paint("  Rollback file for the current database version — "
                        "use it if this is\n  run by hand instead: "
                        f"{journal_root(ns.project_root) / entry / 'restore.sql'}",
                        "dim", en))
        if not confirmed:
            print_doors(token, en,
                        then=f"{invocation()} apply {ns.file} --confirm {token}",
                        head="To apply — the developer approves through the "
                             "first door that works, then the agent confirms:")
    if not confirmed:
        return 0

    # 4. APPLY
    if group == "plsql_source" and load_settings(ns.project_root).get(
            "plscope_on_apply", True):
        # session-scoped: every object applied through pythia builds the
        # PL/Scope index as a side effect; opt out with
        # {"plscope_on_apply": false} in .pythia/settings.json
        with conn.cursor() as cur:
            cur.execute("ALTER SESSION SET plscope_settings = "
                        "'IDENTIFIERS:ALL, STATEMENTS:ALL'")
    with conn.cursor() as cur:
        try:
            cur.execute(stmt)
        except Exception as e:            # noqa: BLE001 - the driver refused
            # the preview and "Snapshot saved" printed before this line ran,
            # so without a closing verdict the run reads like one that landed
            if via == "autopilot":
                lease_note_write(ns.project_root, False, [], entry,
                                 written=False)
            sys.exit(f"FAILED — nothing was written.\n{e}\n"
                     "The object is unchanged. The approval is unspent: retry "
                     "the same file with the same token, or fix the file and "
                     "preview again for a new one.")
        affected = cur.rowcount if group == "data_dml" else None
    if group == "data_dml":
        # the last-millisecond check: the probe ran before this statement, and
        # READ COMMITTED lets the world move in between
        if row_set is not None and affected != row_set["count"]:
            conn.rollback()
            sys.exit(f"Rolled back: the statement touched {affected} rows, but "
                     f"{row_set['count']} were approved.\nNothing was "
                     "committed — the rows moved between the check and the "
                     "write. Preview again.")
        conn.commit()          # DDL commits itself; DML never did until now
    if grant is not None:
        spend_grant(ns.project_root, token)   # one approval, one write

    # 5. VERIFY
    err_rows = []
    if group == "plsql_source":
        _, err_rows = run_query(conn, load_query("compile-errors.sql"),
                                {"s": schema, "n": name})
    # every persistent write is checked for collateral damage: an ALTER TABLE
    # or a REVOKE invalidates dependents just as surely as a package spec
    _, inv_rows = run_query(conn, load_query("invalid-objects.sql"), {"s": schema})
    invalid_after = [(r[0], r[1]) for r in inv_rows]
    broke = newly_invalid(invalid_before, invalid_after)
    meta.update(applied=True, invalid_after=invalid_after, newly_invalid=broke,
                compile_errors=[list(r) for r in err_rows])
    # update the SAME entry in place — a second write_journal_entry would race
    # the timestamp and either collide or split one apply across two entries
    (journal_root(ns.project_root) / entry / "meta.json").write_text(
        json.dumps({"object": name, "type": otype, "created": created,
                    "entry": entry, **meta}, indent=2, default=str) + "\n",
        encoding="utf-8")

    # 6. REPORT
    own_errors = [r for r in err_rows if str(r[0]).upper() == name.upper()]
    ok = not own_errors and not broke
    after_lease = (lease_note_write(ns.project_root, ok, broke, entry)
                   if via == "autopilot" else None)
    if ns.json:
        print(json.dumps({"ok": ok, "applied": True, "object": name,
                          "type": otype, "errors": [list(r) for r in own_errors],
                          "newly_invalid": [list(x) for x in broke],
                          "rows": affected,
                          "restore": (f"{invocation()} journal restore {entry}"
                                      if group in ("plsql_source", "comments")
                                      else None),
                          "no_undo": group not in ("plsql_source", "comments"),
                          "confirmed_via": via,
                          "autopilot": (lease_line(after_lease)
                                        if after_lease else None),
                          "restore_is_drop": created,
                          "restore_blocked_by_policy": (
                              created and undo_group_action(ns) == "deny"),
                          "exit": 0 if ok else 3}))
    else:
        en = getattr(ns, "color", False)
        if ok and group == "data_dml":
            print(paint(f"\n  Applied — {affected} rows changed and "
                        "committed.", "green", en))
        elif ok and group == "comments":
            print(paint(f"\n  Applied comment on {name}.", "green", en))
        elif ok and group != "plsql_source":
            print(paint(f"\n  Applied {group} statement.", "green", en))
        elif ok:
            print(paint(f"\n  Applied {name} ({otype}).", "green", en))
            print(paint("  Compiled clean. No new INVALID objects.", "green", en))
        else:
            if own_errors:
                print(paint(f"\n  Applied {name} ({otype}) — but it did not "
                            "compile cleanly:", "red", en))
                for r in own_errors:
                    print(paint(f"    {r[3]}:{r[4]} {r[5]} {str(r[6]).strip()}",
                                "red", en))
            elif group != "plsql_source":
                print(paint(f"\n  Applied the {group} statement — but other "
                            "objects are INVALID now:", "red", en))
            else:
                print(paint(f"\n  Applied {name} ({otype}) — it compiled, but "
                            "broke other objects:", "red", en))
            if broke:
                print(f"  {len(broke)} objects were VALID before and are INVALID now:")
                for n2, t2 in broke:
                    print(paint(f"    {n2} ({t2})", "red", en))
        if group == "comments":
            print("\n  To undo (puts the previous comment back):")
            print("    " + paint(f"{invocation()} journal restore {entry}",
                                 "cyan", en))
        elif group != "plsql_source":
            # no snapshot was taken, so there is nothing to restore — say that
            # instead of printing a restore command that would be refused
            print(paint("\n  There is no undo: the journal kept the statement "
                        "that ran, not a way back.", "yellow", en))
        else:
            if created:
                print("\n  To undo — note: undo means DROPPING it "
                      "(it did not exist before):")
            else:
                print("\n  To undo:")
            print("    " + paint(f"{invocation()} journal restore {entry}",
                                 "cyan", en))
            if created and undo_group_action(ns) == "deny":
                # the honest half: that restore is a DROP, DROP is structural,
                # and structural is deny — so the line above would be refused
                print(paint("  ...but that restore is a DROP, and structural "
                            "is set to deny, so it will be refused.",
                            "yellow", en))
                print(paint("  The developer decides: "
                            f"{invocation()} policy set structural confirm",
                            "yellow", en))
        if after_lease:
            st = lease_status(after_lease)
            print(paint(f"  {lease_line(after_lease)}",
                        "yellow" if st == "paused" else "dim", en))
            if st == "paused":
                print(paint("  Autopilot stopped itself: show the developer "
                            "this result. Resuming takes a new approval: "
                            f"{invocation()} autopilot request", "yellow", en))
    return 0 if ok else 3


def cmd_apply(conn, schema, ns):
    path = pathlib.Path(ns.file)
    if not path.is_file():
        sys.exit(f"No such file: {path}")
    # utf-8-sig: Windows editors prepend a BOM, which read as a stray byte
    # turned every such file into "cannot classify". A BOM-less file reads
    # identically.
    code = run_apply(conn, schema, ns, path.read_text(encoding="utf-8-sig"))
    if code:
        sys.exit(code)


def find_preview_by_token(root, token):
    """The newest journal entry whose preview carried this token. A human
    approves an object and a diff, never a bare hash — so no entry means no
    approval."""
    for eid in list_journal_entries(root):         # newest first
        try:
            meta = read_journal_entry(root, eid)["meta"]
        except SystemExit:
            continue
        if str(meta.get("token", "")) == token:
            return eid, meta
    return None, None


def token_mismatch_message(ns, row_set):
    """Why the token no longer matches. For DML the interesting answer is
    almost always the rows, and the preview's own journal entry still holds
    what they were — so say which, instead of a generic 'something moved'."""
    base = ("The confirmation token does not match: the file or the "
            "database object changed since that preview. Preview again:\n"
            f"  {invocation()} apply {ns.file}")
    _, meta = find_preview_by_token(ns.project_root,
                                    str(ns.confirm).strip().lower())
    if (meta or {}).get("applied"):
        # the token names a preview that already ran. "the file changed" is
        # the wrong story, and sends the agent hunting a difference that is
        # not there
        return (f"Token {str(ns.confirm).strip().lower()} was already applied "
                "— one approval, one write.\nPreview again, then get the new "
                f"token approved:\n  {invocation()} apply {ns.file}")
    if not row_set:
        return base
    then = (meta or {}).get("row_set") or {}
    if not then or fingerprint_text(then) == fingerprint_text(row_set):
        return base                       # the file moved, not the rows
    if then["count"] != row_set["count"]:
        moved = (f"{then['count']} rows when you approved it, "
                 f"{row_set['count']} now")
    else:
        moved = (f"still {row_set['count']} rows, but not the same ones — the "
                 "row fingerprint changed")
    return ("Refused: the row set moved — " + moved + ".\n"
            "Nothing was written. The approval covered the rows as they were.\n"
            "Preview again to see them as they are now:\n"
            f"  {invocation()} apply {ns.file}")


def approval_card(root, token):
    """What a human approves, in pythia's own words, read from disk and never
    recomputed. Returns (entry, meta, lines). The same text is printed at the
    console, asked with in chat, shown by the MCP elicitation and checked by
    the hook — so what is approved is what was shown, whichever door it came
    through. A token names a preview, an autopilot lease request or a deploy
    batch; anything else is refused."""
    import datetime
    kind, entry, meta = find_approvable(root, token)
    if not meta:
        sys.exit(f"No pending preview carries token {token!r}.\n"
                 "Approve what you have seen: run the preview first, then "
                 "approve the token it prints:\n"
                 f"  {invocation()} apply <file>")
    if kind == "autopilot":
        exp = _parse_iso(meta.get("request_expires_at"))
        if exp is None or datetime.datetime.now() > exp:
            sys.exit(f"Autopilot request {token} expired before it was "
                     "approved. Ask again:\n"
                     f"  {invocation()} autopilot request")
        return entry, meta, lease_card(meta, root)
    if kind == "batch":
        if meta.get("applied"):
            sys.exit(f"Deploy {token} already ran. Preview again for a fresh "
                     f"token:\n  {invocation()} deploy "
                     f"{' '.join(meta.get('files', []))}")
        return entry, meta, batch_card(meta)
    if meta.get("applied"):
        sys.exit(f"Token {token!r} belongs to a preview that was already "
                 "applied.\nPreview again to get a fresh token:\n"
                 f"  {invocation()} apply <file>")
    group = meta.get("group", "")
    row_set = meta.get("row_set")
    lines = []
    if group == "comments":
        stmt = read_journal_entry(root, entry)["after"].strip()
        lines.append(f"Approving a comment on {meta.get('object', '?')} in "
                     f"{meta.get('schema', '?')}:")
        lines += [f"  {ln}" for ln in stmt.splitlines()]
        lines.append("The previous comment is snapshotted — restorable from "
                     "the journal.")
    elif group and group != "plsql_source":
        # no object identity, no snapshot: show the statement itself and say
        # plainly that there is no undo waiting behind it
        stmt = read_journal_entry(root, entry)["after"].strip()
        lines.append(f"Approving a {group} statement on {meta.get('schema', '?')}:")
        lines += [f"  {ln}" for ln in stmt.splitlines()]
        if row_set:
            # the rows themselves, exactly as the preview showed them: what is
            # approved has to be what was seen
            lines.append("")
            lines += render_row_set(row_set, False, indent="  ")
        hurt = destructive_reason(stmt, group)
        if hurt:
            lines.append(f"! DESTRUCTIVE: {hurt}.")
        lines.append("There is no snapshot for this group — after commit it "
                     "cannot be undone from the journal.")
    else:
        lines.append(f"Approving: {meta.get('object', '?')} "
                     f"({meta.get('type', '?')}) in {meta.get('schema', '?')}")
        if meta.get("summary"):
            lines.append(f"Impact: {meta['summary'].lstrip('- ')}")
    lines.append(f"Previewed {entry[:19].replace('T', ' ')} on connection "
                 f"{meta.get('connection') or '?'}.")
    return entry, meta, lines


def squash(text):
    """Whitespace-insensitive form, for 'did the question carry the card'."""
    return " ".join(str(text).split()).lower()


TOKEN_IN_QUESTION = re.compile(r"approve\s+(?:--card\s+)?([0-9a-f]{6,})\b", re.I)


def _tool_input_in(raw, tool_use_id, asked):
    """The input of an AskUserQuestion tool_use in one transcript line, as
    the model sent it (the wire copy when the line keeps one), or None."""
    if b"AskUserQuestion" not in raw:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("type") != "assistant":
        return None
    wire = d.get("wireToolInputs") if isinstance(d.get("wireToolInputs"), dict) else {}
    for c in (d.get("message") or {}).get("content") or []:
        if not (isinstance(c, dict) and c.get("type") == "tool_use"
                and c.get("name") == "AskUserQuestion"):
            continue
        sent = wire.get(c.get("id"))
        sent = sent if isinstance(sent, dict) else c.get("input")
        if not isinstance(sent, dict):
            continue
        if tool_use_id:
            if c.get("id") == tool_use_id:
                return sent
        elif [q.get("question") for q in sent.get("questions") or []
              if isinstance(q, dict)] == asked:
            return sent
    return None


def transcript_tool_input(payload, asked, chunk=1 << 20, limit=64 << 20,
                          retries=2, pause=0.25):
    """What the model itself sent in this AskUserQuestion call, read from
    the session transcript — None when it cannot be found. On current
    Claude Code the dialog writes the person's answer into tool_input (and
    drops any answers the model wrote before it shows), so tool_input alone
    cannot say who answered; the transcript keeps the call as the model sent
    it. Read from the end, where the call is; bounded, and retried briefly
    in case the line is still being written."""
    import time
    path = payload.get("transcript_path")
    tid = payload.get("tool_use_id") if isinstance(payload.get("tool_use_id"), str) else None
    if not isinstance(path, str) or not path:
        return None
    for attempt in range(retries + 1):
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                pos, tail, read = f.tell(), b"", 0
                while pos > 0 and read < limit:
                    step = min(chunk, pos)
                    pos -= step
                    read += step
                    f.seek(pos)
                    lines = (f.read(step) + tail).split(b"\n")
                    tail = lines.pop(0) if pos > 0 else b""
                    for raw in reversed(lines):
                        found = _tool_input_in(raw, tid, asked)
                        if found is not None:
                            return found
        except OSError:
            return None
        if attempt < retries:
            time.sleep(pause)
    return None


def next_door_after_chat(token):
    """Where the agent goes, without asking, when the chat door fails."""
    return (f"Next door, now — no need to ask the developer first: call the "
            f"pythia_approve tool (MCP) with token {token}; if it reports that "
            f"the client did not show its card, or it is not available, relay "
            f"`{invocation()} approve {token}` for the developer's terminal.")


def hook_approve(ns):
    """The chat door. Claude Code runs this as a PostToolUse hook on
    AskUserQuestion, with the payload on stdin. A grant is minted only when
    the developer chose Approve in the dialog AND the question carried
    pythia's own card for that token — otherwise the human approved the
    agent's words, not the preview, and that is no approval.

    Who chose is checked, not assumed. Current Claude Code writes the
    person's answer into tool_input after dropping any the model wrote; the
    transcript keeps what the model sent, so answers in the model's own call
    are refused, and answers nobody can trace to the dialog are too. A
    dialog that closed itself while the developer was away, a typed reply
    instead of a choice, and no answer at all are each named. Every failure
    that is not the developer's own no sends the agent to the next door.
    Everything else is silence: a hook that talks on every question is noise."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
        return
    tin = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    resp = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
    in_input = tin.get("answers") if isinstance(tin.get("answers"), dict) else {}
    answers = resp.get("answers") if isinstance(resp.get("answers"), dict) else {}
    answers = answers or in_input
    idle = resp.get("afkTimeoutMs")
    typed = str(resp.get("response") or "").strip()
    asked = [q.get("question", "") for q in (tin.get("questions") or [])
             if isinstance(q, dict)]
    sent = transcript_tool_input(payload, asked) if in_input else None
    notes, seen = [], set()
    for question in list(answers) + [q for q in asked if q not in answers]:
        m = TOKEN_IN_QUESTION.search(str(question))
        if not m:
            continue
        token = m.group(1).lower()
        if token in seen:
            continue
        seen.add(token)
        if idle:
            notes.append(f"{token}: the dialog closed itself after "
                         f"{round(float(idle) / 1000)} s with nobody at the "
                         "keyboard — no one answered. No grant minted. "
                         + next_door_after_chat(token))
            continue
        if in_input and sent is None:
            notes.append(f"{token}: the answer is in the tool input, and the "
                         "session transcript that shows whether you wrote it "
                         "yourself could not be read — so it cannot count. No "
                         "grant minted. " + next_door_after_chat(token))
            continue
        if in_input and sent.get("answers"):
            notes.append(f"{token}: your own call carried answers — that is "
                         "you choosing, not the developer. No grant minted. Ask "
                         "again without an answers field.")
            continue
        answer = answers.get(question)
        if answer is None or not str(answer).strip():
            if typed:
                notes.append(f"{token}: the developer typed a reply instead of "
                             f"choosing: {typed!r}. That is not an approval and "
                             "no grant was minted — answer them, then ask again.")
                continue
            notes.append(f"{token}: no answer came back — the question never "
                         "reached the developer (on older Claude Code an allow "
                         "rule, a skill's allowed-tools naming AskUserQuestion, "
                         "or a PreToolUse hook with an empty or '*' matcher "
                         "answered it first; `"
                         f"{invocation()} doctor` names such a setting). No grant "
                         "minted. Do not ask the same way again. "
                         + next_door_after_chat(token))
            continue
        if squash(answer) != "approve":
            notes.append(f"{token}: the developer did NOT approve (answered "
                         f"{str(answer)!r}). No grant minted — do not apply; "
                         "ask what should change.")
            continue
        try:
            _, meta, body = approval_card(ns.project_root, token)
        except SystemExit as e:
            notes.append(f"{token}: {e}")
            continue
        if squash("\n".join(body)) not in squash(question):
            notes.append(f"{token}: the question did not carry pythia's approval "
                         "card verbatim, so the developer approved your words, "
                         "not the preview. No grant minted. Run "
                         f"`{invocation()} approve --card {token}` and ask "
                         "again with exactly that text.")
            continue
        msg = grant_approval(ns.project_root, token, meta, "chat",
                             payload.get("session_id"))
        notes.append(f"{token}: approved in chat — {msg}")
    if notes:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "pythia: " + "\n".join(notes)}}))


def cmd_approve(conn, schema, ns):
    """The developer's half of the gate: one human act that lets the agent's
    next `apply --confirm` through — at a real console, or in chat via the
    hook (--hook). --card prints what the agent asks with and mints nothing.
    Touches no database — a terminal with no connection configured can still
    approve."""
    if getattr(ns, "hook", False):
        return hook_approve(ns)
    tokens = [ns.token] if isinstance(ns.token, str) else list(ns.token or [])
    tokens = [str(t).strip().lower() for t in tokens]
    bad = [t for t in tokens if not valid_token(t)]
    if bad:
        sys.exit(f"Not a pythia token: {bad[0]!r} — tokens are the 6, 8 or 10 "
                 "hex digits printed by apply, autopilot request or deploy.")
    card_only = getattr(ns, "card", False)
    if not tokens:
        sys.exit(f"Usage: {invocation()} approve <token> [<token> ...]\n"
                 f"       {invocation()} approve --card <token>")
    if not card_only and not console_session(ns):
        sys.exit(APPROVE_HEADLESS_MSG)
    en = getattr(ns, "color", False)
    out = []
    for token in tokens:
        entry, meta, body = approval_card(ns.project_root, token)
        kind = meta.get("kind", "preview")
        conn_name = meta.get("connection") or meta.get("conn") or ""
        row_set = meta.get("row_set")
        rec, msg = None, None
        if not card_only:
            msg = grant_approval(ns.project_root, token, meta, "console")
            rec = (read_grant(ns.project_root, token) if kind != "autopilot"
                   else read_lease(ns.project_root))
        if ns.json:
            card = f"{invocation()} approve {token}\n" + "\n".join(body)
            out.append({"ok": True, "token": token, "conn": conn_name,
                        "object": meta.get("object", "?"),
                        "type": meta.get("type", "?"),
                        "group": meta.get("group", ""),
                        "rows": (row_set or {}).get("count"),
                        "entry": entry, "card": card,
                        "question": {"question": card, "header": "pythia",
                                     "multiSelect": False,
                                     "options": [
                                         {"label": "Approve",
                                          "description": "mint the one-time grant; the agent then runs apply --confirm"},
                                         {"label": "Reject",
                                          "description": "nothing is written; say what should change"}]},
                        "kind": kind,
                        "minted": rec is not None,
                        "minted_at": (rec or {}).get("minted_at")
                        or (rec or {}).get("approved_at"),
                        "expires_at": (rec or {}).get("expires_at"),
                        "message": msg,
                        "ttl_minutes": GRANT_TTL_MINUTES})
            continue
        print()
        if card_only:
            # plain text on purpose: this goes into a question, not a terminal
            print(f"{invocation()} approve {token}")
            print("\n".join(body))
            continue
        for ln in body:
            warn = ln.startswith(("There is no snapshot", "! DESTRUCTIVE",
                                  "  DESTRUCTIVE"))
            print(f"  {paint(ln, 'yellow', en) if warn else ln}")
        if kind == "autopilot":
            print(paint(f"\n  {lease_line(rec)}", "green", en))
            print("  The agent now applies covered statements directly: "
                  + paint(f"{invocation()} apply <file>", "cyan", en))
            continue
        if kind == "batch":
            print(paint(f"\n  Deploy approved — single use, expires in "
                        f"{GRANT_TTL_MINUTES} minutes.", "green", en))
            print("  The agent may now run:  " + paint(
                f"{invocation()} deploy {' '.join(meta['files'])} --confirm "
                f"{token}", "cyan", en))
            continue
        print(paint(f"\n  Grant minted — single use, expires in "
                    f"{GRANT_TTL_MINUTES} minutes.", "green", en))
        print("  The agent may now run:  "
              + paint(f"{invocation()} apply <file> --confirm {token}", "cyan", en))
    if ns.json:
        print(json.dumps(out[0] if len(out) == 1 else out))


def run_restore(conn, schema, ns):
    """Restore is itself a write: feed the saved statement back through the
    same six steps. There is no second write path and no silent restore."""
    e = read_journal_entry(ns.project_root, ns.id)
    group = e["meta"].get("group", "plsql_source")
    if group not in ("plsql_source", "comments"):
        # render_restore() writes a DROP for every group that has no snapshot,
        # which classifies as structural and dies with a message about the
        # wrong thing. Say what is actually true, once, for all of them.
        tail = ("Flashback Query within undo retention is the only route left, "
                "and it is a DBA's job."
                if group == "data_dml" else
                "Reversing it means writing the opposite statement by hand.")
        sys.exit(f"Refused: entry {ns.id} is a {group} statement, and the "
                 "journal holds no undo for it — only the statement that "
                 f"ran.\n{tail}")
    print(f"Restoring from {ns.id} — this is itself a write and goes through "
          "the full six steps.", file=sys.stderr)
    return run_apply(conn, schema, ns, e["restore"], origin={"restored_from": ns.id})


def cmd_policy(conn, schema, ns):
    if ns.action == "set" and (not ns.group or not ns.value):
        sys.exit(f"Usage: {invocation()} policy set <group> <value>\n"
                 f"Groups: {', '.join(sorted(POLICY_DEFAULTS))}; "
                 "values: allow, confirm, deny.")
    if ns.action == "set":
        eff = load_policy(ns.project_root)
        RANK = {"deny": 0, "confirm": 1, "allow": 2}
        if (RANK[ns.value] > RANK[eff[ns.group][0]]
                and not human_at_the_keyboard()):
            sys.exit(f"Loosening the write policy ({ns.group}: "
                     f"{eff[ns.group][0]} → {ns.value}) is the developer's "
                     "decision, and no terminal is attached to this session."
                     "\nAsk the developer to run this themselves:\n"
                     f"  {invocation()} policy set {ns.group} {ns.value}\n"
                     "(Tightening is always allowed; pipelines set "
                     "PYTHIA_CI=1.)")
        eff[ns.group] = (ns.value, "policy.json")   # validated by argparse choices
        path = policy_path(ns.project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({g: v for g, (v, _) in eff.items()}, indent=2)
                        + "\n", encoding="utf-8")
        print(f"Wrote {path}")
    eff = load_policy(ns.project_root)
    if ns.json:
        print(json.dumps({g: {"value": v, "source": src}
                          for g, (v, src) in eff.items()}))
        return
    print("Effective write policy:")
    for g, (v, src) in eff.items():
        print(f"  {g:<13} {v:<8} ({src})")
    print()
    print(ROLLBACK_TABLE)


def cmd_history(conn, schema, ns):
    """Every version of one object the journal holds, newest first. Compact
    on purpose — an agent reads this to choose an id, not to read source."""
    root = ns.project_root
    name = ns.name.upper()
    rows = []
    for eid in list_journal_entries(root):
        e = read_journal_entry(root, eid)
        m = e["meta"]
        if str(m.get("object", "")).upper() != name:
            continue
        real = e["after"] if (m.get("applied") or m.get("snapshot")) else e["before"]
        kind = ("applied" if m.get("applied") else
                "snapshot" if m.get("snapshot") else "preview")
        rows.append({"entry": eid, "kind": kind,
                     "lines": len(real.splitlines()),
                     "sha": source_sha(real),
                     "rollback_file": str(journal_root(root) / eid
                                          / "restore.sql")})
    if ns.json:
        print(json.dumps({"object": name, "versions": rows}))
        return
    if not rows:
        print(f"-- no journal history for {name}. It is captured the first "
              f"time you run `{invocation()} src {name}` or "
              f"`{invocation()} impact {name}`.")
        return
    prev = None
    for r in rows:                       # newest first; compare to the older one
        older = rows[rows.index(r) + 1] if rows.index(r) + 1 < len(rows) else None
        delta = ""
        if older and older["sha"] != r["sha"]:
            n = r["lines"] - older["lines"]
            delta = f"  {n:+d} lines" if n else "  content changed"
        print(f"  {r['entry']:<44} {r['kind']:<9} {r['lines']:>5} lines{delta}")
        prev = r
    print(f"\n-- every version above has a ready-to-run rollback file:")
    print(f"     {journal_root(root)}\\<entry>\\restore.sql")
    print(f"-- through pythia (previews first, you approve):")
    print(f"     {invocation()} journal restore <entry>")


def cmd_journal(conn, schema, ns):
    root = ns.project_root
    if ns.action == "list":
        ids = list_journal_entries(root)
        if ns.json:
            print(json.dumps(ids))
            return
        if not ids:
            print(f"-- journal is empty ({journal_root(root)})")
            return
        for eid in ids:
            meta = read_journal_entry(root, eid)["meta"]
            state = "applied" if meta.get("applied") else "preview"
            print(f"{eid}  [{state}]")
        return
    if ns.action == "prune":
        import shutil
        removed = 0
        # A preview wrote nothing to the database, but its restore.sql holds
        # the version that was live at the time — the only rollback there is
        # for a change the developer then ran by hand. So drop a preview only
        # when its rollback is byte-identical to one a newer entry already
        # keeps: redundant captures go, unique ones never do.
        kept = {}
        for eid in list_journal_entries(root):        # newest first
            e = read_journal_entry(root, eid)
            meta, obj = e["meta"], str(e["meta"].get("object", ""))
            if meta.get("applied") or meta.get("snapshot"):
                kept.setdefault(obj, set()).add(e["restore"])
                continue
            if e["restore"] in kept.get(obj, set()):
                shutil.rmtree(journal_root(root) / eid)
                removed += 1
            else:
                kept.setdefault(obj, set()).add(e["restore"])
        if ns.json:
            print(json.dumps({"pruned": removed}))
        else:
            print(f"-- pruned {removed} redundant previews; applied entries, "
                  "snapshots, and any preview holding a rollback nothing "
                  "else has are all kept")
        return
    if not ns.id:
        sys.exit(f"Usage: {invocation()} journal "
                 "{list | show <id> | diff <id> | export <id> | prune | restore <id>}")
    e = read_journal_entry(root, ns.id)
    if ns.action == "show":
        print(json.dumps(e["meta"], indent=2))
    elif ns.action == "diff":
        text, changed = render_diff(e["before"], e["after"])
        print(text or "-- no difference")
        print(f"-- {changed} lines changed", file=sys.stderr)
    elif ns.action == "export":
        out = pathlib.Path(f"{ns.id}_{ns.what}.sql")
        out.write_text(e[ns.what], encoding="utf-8")
        print(f"Wrote {out}")
    else:
        sys.exit("unreachable: restore is dispatched with a connection in main")


def _schema_objects(conn, schema):
    _, rows = run_query(conn, load_query("object-names.sql"), {"s": schema})
    return [(r[0], r[1]) for r in rows]


BRIEF_GUIDE = """\
PYTHIA HARNESS ACTIVE - Learn, Ask, Do. Before any Oracle/PL-SQL action:

ROUTE  build/change with open decisions -> pythia-spec asks FIRST. tables,
       columns, comments -> pythia-table. slow SQL -> pythia-tune. package,
       trigger design -> pythia-package. scripts, releases -> pythia-deploy.
       explain -> explore. review -> review. landing a change -> apply.
LEARN  impact before any change. Ask the live database, never a dump. Read
       .pythia/conventions.md if present; `pythia lint <files>` before apply
       - a change's files in one run, so comments count across them.
       `pythia connections` - never open connections.json (passwords).
ASK    relay apply/deploy previews verbatim and wait for a real yes. >=10
       dependents or cross-schema: show the developer first. Refusals and
       exit 3 (written-but-broken) are relayed, never routed around. Never
       read exit codes through a pipe.
DO     writes only via `pythia apply` / `pythia deploy`. Approval doors, the
       first that works, the next unasked if one fails: AskUserQuestion with
       `pythia approve --card <token>` verbatim (Claude Code), pythia_approve
       (MCP; Codex starts here), `pythia approve <token>` (terminal). Never
       mint it. A lease lands covered writes unasked; report each (autopilot
       report). Evidence before done (pythia-verify). Contract: `pythia guide`.
"""


AGENTS_BEGIN = "<!-- pythia:begin (managed by `pythia install`; edit outside this block) -->"
AGENTS_END = "<!-- pythia:end -->"


def agents_md_block():
    """The pythia section for AGENTS.md — Codex's always-loaded instruction
    file. Body is BRIEF_GUIDE verbatim (the same constant `guide --brief`
    prints and the Claude SessionStart hook runs, so the two cannot drift),
    plus the lines Codex needs that Claude gets from its hook: where the
    approval door is, since Codex has no chat-mint. Marker-wrapped so a re-run
    replaces it in place and the developer's own text is untouched."""
    return (f"{AGENTS_BEGIN}\n"
            "## pythia — developing PL/SQL on Oracle\n\n"
            f"{BRIEF_GUIDE.rstrip()}\n\n"
            "APPROVAL ON CODEX: your approval door is the `pythia_approve` MCP "
            "tool, not AskUserQuestion (Codex has none). After `pythia apply "
            "<file>` (or `pythia deploy`, or `pythia autopilot request`) prints "
            "a token, call the `pythia_approve` tool with that token — the "
            "developer answers a select prompt, and on Approve it is recorded. "
            "Then YOU run the command the tool result names (`apply <file> "
            "--confirm <token>`, `deploy <files> --confirm <token>`, or plain "
            "`apply` under an autopilot lease) yourself; do not stop at the "
            "preview or hand the developer a command to type. If the tool is "
            "not available (older Codex), or reports that the client did not "
            "show its card, relay `pythia approve <token>` for their terminal "
            "at once.\n"
            f"{AGENTS_END}")


def claude_md_block():
    """The pythia section for CLAUDE.md — the file Claude Code itself loads
    every session, whether or not the hooks are wired, trusted or restarted.
    Same BRIEF_GUIDE as AGENTS.md and the SessionStart hook (one source, no
    drift), plus what only Claude needs: its approval doors in order, and the
    standing rules an agent breaks most when nothing reminds it."""
    return (f"{AGENTS_BEGIN}\n"
            "## pythia — developing PL/SQL on Oracle\n\n"
            f"{BRIEF_GUIDE.rstrip()}\n\n"
            "APPROVAL ON CLAUDE CODE: first door is AskUserQuestion — Claude "
            "Code's own dialog, shown in every client (terminal, desktop, web, "
            "phone). Its text is exactly the output of `pythia approve --card "
            "<token>`, options Approve / Reject, never with answers filled in. "
            "When pythia's hook says the answer did not count for any reason "
            "but the developer's own Reject, go straight on — without asking — "
            "to the `pythia_approve` tool (MCP server `pythia`); if that "
            "reports the client did not show its card, relay `pythia approve "
            "<token>` for the developer's terminal. Then run the command the "
            "approval names yourself.\n\n"
            "HOOKS NOT RUNNING? pythia's SessionStart hook prints a `STATE` line "
            "(connections, autopilot, approval door). No STATE line this session, "
            "or no `pythia_approve` tool (MCP server `pythia` failed to "
            "connect): pythia's hooks cannot start on this machine, so the "
            "sqlplus guard and the AskUserQuestion door are off. Say so to the "
            "developer before any write and ask them to run `pythia doctor`; "
            "every rule here still binds.\n\n"
            "STANDING RULES: writes only through `pythia apply` / `pythia "
            "deploy` (never sqlplus, never SQLcl run-sql); `pythia impact` "
            "before any change; `pythia lint` before any apply; never open "
            ".pythia/connections.json (`pythia connections`); exit 3 is "
            "written-but-broken, never done; under an autopilot lease report "
            "every write (`pythia autopilot report`), and a paused lease means "
            "stop and show the developer. Full contract: `pythia guide`; the "
            "skills (`using-pythia` routes) carry the workflows.\n"
            f"{AGENTS_END}")


def merge_marked_block(path, block):
    """Write a pythia block into a developer's instruction file. The file is
    theirs, shared with every agent that reads it, so only the delimited
    block is ours: present -> replaced in place, absent -> appended below
    their text, everything else byte-identical. Returns (path, action) where
    action is 'created' | 'added' (joined their file) | 'updated' (a block
    replaced) | 'unchanged'."""
    path = pathlib.Path(path)
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    if old is None:
        new, action = block + "\n", "created"
    elif AGENTS_BEGIN in old and AGENTS_END in old:
        pre = old[:old.index(AGENTS_BEGIN)]
        post = old[old.index(AGENTS_END) + len(AGENTS_END):]
        new = pre + block + post
        action = "unchanged" if new == old else "updated"
    else:
        tail = "" if old.endswith("\n\n") else "\n" if old.endswith("\n") else "\n\n"
        new, action = old + tail + block + "\n", "added"
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    return path, action


def merge_agents_md(path):
    """The pythia block in an AGENTS.md (Codex and AGENTS.md agents)."""
    return merge_marked_block(path, agents_md_block())


def merge_claude_md(path):
    """The pythia block in a CLAUDE.md (Claude Code)."""
    return merge_marked_block(path, claude_md_block())


OPERATING_GUIDE = """\
THE OPERATING MODEL — Learn, Ask, Do (Hoc - Hoi - Lam)

pythia is a harness: a book of working rules for an AI agent sitting next to
a developer. Every task moves through the same three movements. If you cannot
say which movement you are in, you are in none of them.

=== 1. LEARN — understand before proposing ======================
Nothing here writes. Reading is free; guessing is not.

  the problem's shape     deps · impact · plscope     the exact graph, not a skim
  the schema's truth      src · args · cols · ddl · errors · invalid · check · ls · grep · sql
  what you can reach      connections - names, users, targets. NEVER open
                          connections.json yourself: it holds passwords, a
                          permission gate should stop you, and this command
                          is the answer you were reaching for.
  the house style         conventions (--scan / --check) · the project's conventions.md
                          lint <files> - the built-in standards plus the LEAD's
                          rules from conventions.json, before anything is applied
  how it is done here     similar · history            neighbours to imitate, versions that exist
  tables and docs         comments <TABLE> (--scaffold) · stats <TABLE> - what
                          is documented, what the optimizer believes, which
                          foreign keys lack an index
  performance             explain "select ..." - the plan, parsed never run
  the harness itself      doctor (--db) - hooks wired, approval doors working,
                          the settings that break AskUserQuestion, the database
                          environment

Iron laws: no proposal before the blast radius is known; no line written
before the neighbours have been read.

=== 2. ASK — the questions are the method =======================
Stop at exactly these moments; a guess past any of them is a defect.

  the request is open     a feature with more than one reasonable shape:
                          list the spec decisions you would otherwise settle
                          silently - options, trade-offs, a recommendation -
                          and get them chosen BEFORE building. Building first
                          turns the developer's choice into agree-or-rework.
                          Then OFFER a written spec/plan - the developer may
                          skip the documents; the questions were the point.
  before any write        relay the full preview (diff, dependents, warnings)
                          verbatim, then wait. A compliment is not a yes.
  blast radius >= 10      or anything cross-schema: show the developer the
                          list BEFORE writing code.
  truths disagree         document says one thing, schema another - ask which:
                          rule nobody follows, new-code-only, or drift.
  policy refuses          relay the refusal (policy shows the rules). Never
                          route around it.
  it broke                exit 3 = written but broken. Say exactly that, with
                          the rollback line. journal (show/diff) is your evidence.

=== 3. DO — act inside a pipeline that cannot lie ===============
One door for writes: snapshot -> impact -> preview -> token -> approve ->
apply -> verify -> report.

  approve                 THE DEVELOPER'S ACT: mints the one-time grant that
                          --confirm requires. Three doors, all theirs, in this
                          order: Approve to your AskUserQuestion (question
                          text = `approve --card <token>`, verbatim - the hook
                          mints only then; Claude Code), the pythia_approve
                          MCP tool (where Codex starts), or `approve <token>`
                          at their own terminal. A door that fails says so
                          and names the next. An agent never mints it.
  apply                   the six-step write; --confirm binds to the preview
                          AND to the developer's approval
  deploy                  a release script, or several, statement by statement
                          through the same six steps, in order; one approval
                          for the set; stops at the first refusal or exit 3
  autopilot               the developer delegates a session ONCE: a lease on one
                          connection, named groups, hours and a write budget.
                          While active, apply/deploy land covered statements
                          without asking; destructive ones, lint errors and
                          protected connections never; breaking other objects
                          pauses it. `autopilot report` lists every write.
  journal restore         undo, through the same six steps and the same gate
  unistr                  exact non-ASCII literals for what you are writing
  mcp                     the write-free MCP approver: Codex's first door,
                          Claude Code's second. The agent calls its
                          pythia_approve tool, the client shows the card as a
                          select prompt, and the developer's Approve mints the
                          grant. It never writes; apply still verifies it.
  install · agent-user · guide    setting the harness itself up
  hook                    what Claude Code runs: session-start (guide + live
                          state), pre-tool (reads pass, approved writes pass,
                          sqlplus/run-sql writes are denied), post-tool (the
                          verdict rules next to an apply result)
  skill new · skill lint  grow the team's own skills in this pack's format

The CLI enforces the gates: headless --yes is refused, policy cannot be
loosened without a human at a terminal, an autopilot lease starts only from
the developer's Approve, the snapshot cannot be switched off.

using-pythia routes to the right skill before any action; the skills
carry the full method (pythia-spec, -explore, -impact, -conventions, -write,
-table, -package, -tune, -apply, -deploy, -autopilot, -verify, -review,
-setup, -skill-author). No skill support on this platform?
This page is the contract; follow it as written. `pythia guide --brief`
is its one-page form, sized for a session preamble.
"""


def connection_summary(cfg):
    """Everything about the configured connections except the secrets.

    An agent has to know which connections exist. Without a sanctioned way to
    ask, it reads connections.json itself — which is exactly the access a
    permission classifier should stop, and did. So this exists, and it is
    built to be provably safe: fields are copied in by name, never by
    iterating the entry, so a key added to the config later cannot leak
    through here by accident.
    """
    cfg = dict(cfg or {})
    default = cfg.pop("default", None)
    rows = []
    for name, entry in cfg.items():
        if not isinstance(entry, dict):
            continue
        dsn = entry.get("dsn") or ""
        if not dsn and entry.get("host"):
            svc = entry.get("service_name") or entry.get("sid") or ""
            dsn = f"{entry['host']}:{entry.get('port', 1521)}"
            if svc:
                dsn += f"/{svc}"
        user = str(entry.get("user") or "")
        rows.append({
            "name": name,
            "user": user,
            "target": dsn or "\u2014",
            "schema": (entry.get("schema") or user.split("[")[-1].rstrip("]")
                       or "\u2014").upper(),
            "default": isinstance(default, str) and default.upper() == name.upper(),
        })
    return rows


def cmd_connections(conn, schema, ns):
    cfg, _ = find_config(pathlib.Path.cwd(), os.environ)
    rows = connection_summary(cfg)
    if ns.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No connections configured. "
              f"{invocation()} install scaffolds .pythia/connections.json.")
        return
    print(f"{'':1} {'NAME':<16} {'USER':<26} {'SCHEMA':<20} TARGET")
    for r in rows:
        mark = "*" if r["default"] else " "
        print(f"{mark} {r['name']:<16} {r['user']:<26} {r['schema']:<20} "
              f"{r['target']}")
    print("\n* default. Passwords are never printed — this command reads the "
          "config so you\n  do not have to open it. Pick one with --conn NAME.")


def cmd_guide(conn, schema, ns):
    print(BRIEF_GUIDE if getattr(ns, "brief", False) else OPERATING_GUIDE)


def cmd_conventions(conn, schema, ns):
    root = ns.project_root
    if getattr(ns, "init", False):
        made = scaffold_conventions(root)
        for path in made:
            print(f"Created {path}")
        if not made:
            print(f"Both files already exist in "
                  f"{pathlib.Path(root) / CONFIG_DIR} — left untouched.")
        else:
            print(NL + "Edit the patterns to match this schema — "
                  f"`{invocation()} conventions --scan` reads them off it.")
        return

    if getattr(ns, "scan", False):
        proposed = scan_conventions(_schema_objects(conn, schema))
        if ns.json:
            print(json.dumps(proposed, indent=2))
            return
        print(f"Patterns read off {schema}. Review against your own standards, "
              "then save as" + NL + f"{CONFIG_DIR}/conventions.json:" + NL)
        print(json.dumps(proposed, indent=2))
        if not proposed["naming"]:
            print(NL + "Nothing proposed: too few objects, or names with no "
                  "shared shape.")
        return

    if getattr(ns, "check", False):
        conv = load_conventions(root)
        if not conv:
            sys.exit(f"No conventions to check. {invocation()} conventions "
                     "--scan proposes some from this schema.")
        cov = pattern_coverage(conv, _schema_objects(conn, schema))
        if ns.json:
            print(json.dumps(cov, indent=2))
            return
        worst = 0
        for otype, s in sorted(cov.items()):
            print(f"{otype:<14} {s['matched']}/{s['total']}  "
                  f"{coverage_verdict(s['matched'], s['total'])}")
            if s["misses"]:
                shown, more = s["misses"][:8], len(s["misses"]) - 8
                print("               not matching: " + ", ".join(shown)
                      + (f", +{more} more" if more > 0 else ""))
                worst = max(worst, len(s["misses"]))
        if worst:
            print(NL + "Every name above warns on `apply`. Widen the pattern "
                  "if it is the rule that is wrong," + NL + "or record them in "
                  "conventions.md as deliberate exceptions.")
        return

    conv = load_conventions(root)
    if ns.json:
        print(json.dumps(conv or {}))
        return
    if not conv:
        print("No project conventions configured.")
        print(f"  {invocation()} conventions --scan   read patterns off this schema")
        print(f"  {invocation()} conventions --init   start from a blank template")
        return
    print("Naming patterns — apply previews warn when a name drifts:")
    for otype, pattern in conv.get("naming", {}).items():
        print(f"  {otype:<13} {pattern}")
    md = pathlib.Path(root) / CONFIG_DIR / "conventions.md"
    if md.is_file():
        print(NL + f"Prose rules for agents: {md}")
    print(f"{NL}Measure them against the schema: {invocation()} conventions --check")

def agent_user_sql(owner, agent, password, exists):
    """exists True: the user is already there — CREATE would be ORA-01920,
    so reset the password and unlock in one statement (harmless if it was
    never locked). exists None: no database to ask — emit CREATE plus the
    fallback as a comment so the DBA can pick."""
    if exists:
        line = (f'ALTER USER {agent} IDENTIFIED BY "{password}" ACCOUNT '
                f"UNLOCK;  -- user already exists; CREATE would be ORA-01920")
    else:
        line = f'CREATE USER {agent} IDENTIFIED BY "{password}";'
        if exists is None:
            line += ("\n-- (if the user already exists — ORA-01920 — run "
                     "instead:\n--  ALTER USER "
                     f'{agent} IDENTIFIED BY "{password}" ACCOUNT UNLOCK;)')
    return AGENT_USER_SQL.format(owner=owner, agent=agent, create_line=line)


def agent_password():
    """Random password satisfying common Oracle verify functions: letter
    first, upper+lower+digit+special, 16+ chars. Always quoted in SQL."""
    import secrets
    import string
    body = "".join(secrets.choice(string.ascii_letters + string.digits)
                   for _ in range(12))
    return "Ag" + body + "#7"


def save_agent_connection(root, base_name, base, agent, password):
    """Add <base>_agent alongside the owner entry (never overwrite it) and
    make it the default. Returns the new entry name."""
    path = pathlib.Path(root) / CONFIG_DIR / CONFIG_NAME
    cfg = json.loads(path.read_text(encoding="utf-8"))
    owner = (base.get("schema") or base["user"]).upper()
    entry = dict(base)
    entry["user"] = f"{agent.lower()}[{owner.lower()}]"
    entry["password"] = password
    entry["schema"] = owner
    name = f"{base_name}_agent"
    cfg[name] = entry
    cfg["default"] = name
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return name


def cmd_agent_user(conn, schema, ns):
    cwd = pathlib.Path.cwd()
    cfg, root = find_config(cwd, os.environ)
    base_name, base = resolve_connection(cfg, ns.conn, os.environ, cwd, root)
    owner = (base.get("schema") or base["user"]).upper()
    if "[" in base["user"]:
        sys.exit(f"Connection {base_name} already uses a proxy user "
                 f"({base['user']}) — nothing to set up.")
    agent = (ns.name or f"{owner}_AGENT").upper()
    password = agent_password()
    exists, dangerous = None, None
    if conn is not None:
        _, rows = run_query(conn, "select username from all_users "
                                  "where username = :n", {"n": agent})
        exists = bool(rows)
        _, prows = run_query(conn, load_query("session-privileges.sql"))
        dangerous = [r[0] for r in prows]
    sql = agent_user_sql(owner, agent, password, exists)
    saved = None
    if ns.save:
        saved = save_agent_connection(root, base_name, base, agent, password)
    if ns.json:
        print(json.dumps({
            "owner": owner, "agent": agent, "password": password, "sql": sql,
            "agent_user_exists": exists, "saved_connection": saved,
            "owner_connection": base_name,
            "check_will_warn": bool(dangerous) if dangerous is not None else None,
            "owner_dangerous_privs": dangerous,
            "next": ["have a DBA run the sql", f"{invocation()} check"]}))
        return
    print(sql)
    print("-- The password is regenerated on EVERY run — only the SQL from")
    print("-- this exact run matches what --save writes. One run, not two.")
    if dangerous:
        print(f"\n! Even after this, {invocation()} check will STILL warn: "
              f"the owner {owner} holds\n  {', '.join(dangerous[:3])}"
              + ("…" if len(dangerous) > 3 else "")
              + " — every proxy session inherits it.\n"
              "  Trim the owner's grants to reach the no-warning goal state "
              "(see README, Security).")
    elif dangerous is not None:
        print(f"\nOwner grants look clean: after the DBA runs this, "
              f"{invocation()} check should pass with no warning.")
    if saved:
        print(f"\nSaved connection '{saved}' (now the default) in "
              f"{pathlib.Path(root) / CONFIG_DIR / CONFIG_NAME} — its "
              f"password matches the SQL above;\n"
              f"the owner entry '{base_name}' is untouched — switch back "
              f"any time with --conn {base_name}.")
        print(f"\nNext: relay the SQL above to the developer verbatim for a "
              f"DBA to run, then: {invocation()} check")
    else:
        print(f"-- Nothing was saved. Use --save to also write this "
              f"credential to connections.json as '{base_name}_agent' in "
              f"the same run.")


# unistr escapes. Single quote doubles to '' per SQL (a \' is ORA-01756 for
# Oracle); backslash doubles for unistr itself.
UNISTR_ESC = {0: r"\0", 8: r"\b", 9: r"\t", 10: r"\n", 13: r"\r",
              11: r"\v", 12: r"\f", 92: "\\\\", 39: "''"}


def to_unistr(text):
    r"""A National-charset-safe Oracle literal for any text. Non-ASCII goes
    to \XXXX escapes, so the statement survives every client/DB charset on
    the way in — the standard way to keep Vietnamese messages exact."""
    out = []
    for ch in text:
        cp = ord(ch)
        if cp in UNISTR_ESC:
            out.append(UNISTR_ESC[cp])
        elif 0x1F < cp < 0x7F:
            out.append(ch)
        elif cp > 0xFFFF:
            out.append("\\U%08X" % cp)   # beyond the BMP: \U + 8 hex
        else:
            out.append("\\%04X" % cp)
    return "unistr('%s')" % "".join(out)


def cmd_unistr(conn, schema, ns):
    text = " ".join(ns.text) if ns.text else sys.stdin.read().rstrip("\n")
    u = to_unistr(text)
    print("'loi:'||%s||':loi'" % u if ns.loi else u)


DEFAULT_SKILLS_SOURCE = "thaildhe172591/pythia"

# Kept byte-identical to examples/connections.example.json —
# tests/test_install.py fails on any drift.
CONNECTIONS_TEMPLATE = """{
  "default": "dev",

  "dev": {
    "host": "",
    "port": 1521,
    "service_name": "",
    "sid": "",
    "dsn": "",
    "user": "",
    "password": "",
    "schema": ""
  },
  "staging": {
    "host": "",
    "port": 1521,
    "service_name": "",
    "sid": "",
    "dsn": "",
    "user": "",
    "password": "",
    "schema": ""
  }
}
"""


def scaffold_config(root):
    """Create .pythia/connections.json from the template. An existing file is
    never touched — it may hold real credentials."""
    path = pathlib.Path(root) / CONFIG_DIR / CONFIG_NAME
    if path.is_file():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    # it is where the passwords go: owner-only from the first byte
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(CONNECTIONS_TEMPLATE)
    return path, True


def run_skills_add(source, interactive=False, glob=False):
    """Install the skill pack via the skills CLI. Returns its exit code, or
    None when npx is absent — the caller then copies the bundled pack, so a
    machine with only Python still gets the whole kit. At a TTY the CLI's own
    interactive agent picker is left on; piped/CI runs get -y."""
    import shutil
    import subprocess
    npx = shutil.which("npx")
    if npx is None:
        return None
    return subprocess.run(skills_add_cmd(npx, source, interactive,
                                         glob=glob)).returncode


def skills_add_cmd(npx, source, interactive, glob=False):
    """Interactive: the CLI's own agent picker. Non-interactive project
    installs pin one agent target so nothing is double-installed; global
    installs let the CLI place per-agent copies under the home directory."""
    cmd = [npx, "skills", "add", source]
    if glob:
        cmd.append("-g")
    if not interactive:
        cmd.append("-y")
        if not glob:
            cmd += ["-a", "claude-code"]
    return cmd


LEGACY_SKILLS = ("plsql-setup", "plsql-explore", "plsql-impact",
                 "plsql-write", "plsql-apply", "plsql-review",
                 "plsql-skill-author")


def pack_names():
    return [p.name for p in sorted(SKILLS_DIR.iterdir())
            if (p / "SKILL.md").is_file()]


def global_pack_present(home=None):
    """Is the skill pack already installed machine-wide? Then a project copy
    would only duplicate every entry in the agent's menu."""
    home = pathlib.Path(home) if home else pathlib.Path.home()
    return any((home / rel / "pythia-apply" / "SKILL.md").is_file()
               for rel in (".claude/skills", ".agents/skills",
                           ".config/agents/skills"))


def copy_bundled_skills(base_dir):
    """No-Node fallback: copy the wheel-bundled pack into BOTH conventional
    roots — <base>/.claude/skills/ (Claude Code) and <base>/.agents/skills/
    (Codex, and ~/.agents/skills on -g). base_dir is the project root, or the
    home directory for -g. Node users get the same reach from `npx skills
    add`; this is the machine that has only Python.

    Trade-off, stated plainly: this reverses an earlier one-root-only choice.
    A Claude Code version that reads both roots may list a pack entry twice —
    accepted, because a Codex user without Node otherwise gets no skills at
    all, and Codex reads only .agents/skills.

    Both destinations are cleared link-first before the copy. An earlier
    `npx skills add` leaves .claude/skills/<name> as a symlink into
    .agents/skills/<name>; removing both links first means each copytree
    writes a fresh real directory, never through a link into a target the
    other copy would then overwrite. Only the pack's own names are touched;
    other skills in those directories are left alone."""
    import shutil
    base = pathlib.Path(base_dir)
    claude_root = base / ".claude" / "skills"      # Claude Code reads this
    agents_root = base / ".agents" / "skills"      # Codex reads this
    for pack in sorted(SKILLS_DIR.iterdir()):
        if not (pack / "SKILL.md").is_file():
            continue
        _remove_link_first(claude_root / pack.name)
        _remove_link_first(agents_root / pack.name)
        shutil.copytree(pack, claude_root / pack.name)
        shutil.copytree(pack, agents_root / pack.name)
    clean_legacy_skills(base_dir)
    return [claude_root, agents_root]


def _remove_link_first(path):
    """Windows junctions defeat is_symlink(); unlink/rmdir take out a file,
    symlink, junction or empty dir without touching any target — only a
    real populated directory needs rmtree."""
    import shutil
    if not os.path.lexists(path):
        return
    try:
        path.unlink()
    except OSError:
        try:
            os.rmdir(path)
        except OSError:
            shutil.rmtree(path)


def clean_legacy_skills(base_dir):
    """Remove stale copies under the pack's old plsql-* names from both
    conventional roots below base_dir (project root or home)."""
    for rel in (".agents/skills", ".claude/skills"):
        for old_name in LEGACY_SKILLS:
            _remove_link_first(pathlib.Path(base_dir) / rel / old_name)


# What `install` wires into Claude Code's settings.json: the two hooks and
# the deny rule, verbatim from examples/claude-code-settings.example.json (a
# test keeps them equal). The allow list and autoMode stay in the example —
# those are the developer's permission preferences, not pythia's to set.
CLAUDE_HOOKS = {
    "SessionStart": [{"hooks": [
        {"type": "command", "command": "python -m pythia hook session-start"}]}],
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python -m pythia hook pre-tool",
             "timeout": 15}]},
        {"matcher": "mcp__.*__run-sql.*", "hooks": [
            {"type": "command", "command": "python -m pythia hook pre-tool",
             "timeout": 15}]}],
    "PostToolUse": [
        {"matcher": "AskUserQuestion", "hooks": [
            {"type": "command", "command": "python -m pythia approve --hook",
             "timeout": 15}]},
        {"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python -m pythia hook post-tool",
             "timeout": 15}]}],
}
CLAUDE_DENY = ["Bash(pythia approve --hook*)",
               "Bash(python -m pythia approve --hook*)"]
# a hook already present in any spelling (python3, the plugin's script path)
# is recognised by what it runs, not by how it invokes it. "guide --brief" is
# the session hook before 0.15.0: upgraded in place, never doubled.
HOOK_MARKS = ("hook session-start", "hook pre-tool", "hook post-tool",
              "approve --hook", "guide --brief")
LEGACY_HOOK_COMMANDS = {"guide --brief": "hook session-start"}
# the PreToolUse matchers must never be "" or "*": a hook that catches
# AskUserQuestion makes it return empty (anthropics/claude-code#13439)
MCP_SERVER_ENTRY = {"command": "python", "args": ["-m", "pythia", "mcp"]}


def _hook_mark(command):
    return next((m for m in HOOK_MARKS if m in str(command)), None)


def _pythia_mark(command):
    """The mark, when what runs it is a pythia launcher (`... -m pythia`,
    `pythia`, a pythia.py path) — `hook post-tool` alone is two words any
    tool may use, and another tool's hook is not ours to count or respell."""
    cmd = str(command)
    mark = _hook_mark(cmd)
    if not mark:
        return None
    prefix = cmd[:cmd.index(mark)].strip()
    return mark if re.search(r"(^|[\s/\\\"'])pythia(\.py)?[\"']?$", prefix) else None


def local_interpreter(obj):
    """The hook/MCP entry as this machine can run it. The shipped spelling is
    `python`, which every Windows install has and many Linux/macOS ones do
    not — there, a hook that cannot start fails silently and the door it was
    meant to open stays shut. Only rewritten when `python` is really absent."""
    import shutil
    if shutil.which("python"):
        return obj
    stem = pathlib.Path(sys.executable).stem or "python3"
    text = json.dumps(obj).replace('"python -m pythia', f'"{stem} -m pythia')
    out = json.loads(text)
    if isinstance(out, dict) and out.get("command") == "python":
        out["command"] = stem
    return out


# --- the launcher: how Claude Code's hooks and the MCP approver start pythia ---
#
# A hook that cannot start fails open: Claude Code treats any exit but 2 as a
# non-blocking error, so the sqlplus guard lets the write through and the MCP
# door never connects (field test 0.15.1: pythia in a venv, Claude Code started
# where `python` was the system interpreter). So the launcher is chosen by what
# really starts pythia, and a machine-specific one never enters a shared file.

PORTABLE_LAUNCHER = "python -m pythia"
_INTERPRETER_RE = re.compile(r"^(python[\d.]*|py)(\.exe)?$", re.I)


def in_virtualenv(prefix=None, base=None, env=None):
    """Does pythia run from an environment that `python` reaches only while
    it is activated — a venv, pipx, uv, or any conda environment (conda's
    own envs look like a base install, so conda-meta gives them away)?"""
    real = prefix is None
    prefix = sys.prefix if prefix is None else prefix
    base = getattr(sys, "base_prefix", sys.prefix) if base is None else base
    env = os.environ if env is None else env
    norm = lambda p: os.path.normcase(os.path.abspath(p))       # noqa: E731
    conda = (pathlib.Path(prefix) / "conda-meta").is_dir() or bool(
        env.get("CONDA_PREFIX") and norm(env["CONDA_PREFIX"]) == norm(prefix))
    return bool(prefix != base or conda or (real and hasattr(sys, "real_prefix")))


def path_lookup(name, path=None, pathext=None):
    """The file a bare command name runs from PATH alone. Windows would try
    the running interpreter's directory and the current directory first —
    where a repository can drop a python.exe — so absolute PATH entries only,
    and the caller runs the path this returns."""
    path = os.environ.get("PATH", "") if path is None else path
    exts = [""]
    if os.name == "nt":
        pe = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD") \
            if pathext is None else pathext
        exts = ([""] if os.path.splitext(name)[1] else []) + \
            [e for e in pe.split(";") if e]
    for d in path.split(os.pathsep):
        if not d or not _is_abs_path(d):
            continue
        for e in exts:
            f = os.path.join(d, name + e)
            if os.path.isfile(f) and os.access(f, os.X_OK):
                return f
    return None


def _is_abs_path(p):
    """Absolute on any OS, judged by spelling: /x, C:/x, C:\\x, //host/x."""
    return bool(re.match(r"^([A-Za-z]:[/\\]|[/\\])", p))


def git_tracks(root, rel):
    """Is `rel` committed in the repository holding `root`? A tracked
    .claude/settings.local.json is repository content, not this machine's."""
    import subprocess
    git = path_lookup("git")
    if not git:
        return False
    try:
        r = subprocess.run([git, "-C", str(root), "ls-files", "--error-unmatch",
                            "--", rel], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def launcher_argv(launcher):
    """The launcher's words, quotes removed. Shared and machine-specific
    spellings alike are written with forward slashes (as_posix), so one split
    serves every OS."""
    import shlex
    words = shlex.split(launcher, posix=False)
    return [w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in "\"'" else w
            for w in words]


def launcher_text(argv):
    """argv back to a launcher: a path is quoted, a bare name is not."""
    return " ".join(f'"{w}"' if re.search(r"[/\\\s]", w) else w for w in argv)


def plain_launcher(launcher):
    """argv when the launcher is exactly an interpreter plus `-m pythia` or a
    pythia.py path, else None. doctor runs nothing else: hook commands in a
    project's settings are repository content."""
    try:
        argv = launcher_argv(launcher)
    except ValueError:
        return None
    if not argv or not _INTERPRETER_RE.match(re.split(r"[/\\]", argv[0])[-1]):
        return None
    # a path is absolute or it is not run: a relative one resolves against
    # whatever directory the run happens in — the repository's, often
    if re.search(r"[/\\]", argv[0]) and not _is_abs_path(argv[0]):
        return None
    if argv[1:] == ["-m", "pythia"] or (
            len(argv) == 2 and _is_abs_path(argv[1])
            and re.search(r"[/\\]pythia\.py$", argv[1])):
        return argv
    return None


def launcher_starts(launcher, timeout=20):
    """(True, "") when the launcher starts pythia, else (False, why). Runs
    `<launcher> --help` with no shell, in an empty directory, so a pythia.py
    lying in the current directory is never what answers."""
    import subprocess
    import tempfile
    try:
        argv = launcher_argv(launcher)
        if argv and not re.search(r"[/\\]", argv[0]):
            found = path_lookup(argv[0])
            if not found:
                return False, f"`{argv[0]}` not found on PATH"
            argv = [found] + argv[1:]
        with tempfile.TemporaryDirectory() as empty:
            # pythia writes UTF-8 whatever the console; strict cp1252 would
            # turn a Vietnamese line of help into a false "cannot start"
            r = subprocess.run(argv + ["--help"], cwd=empty,
                               stdin=subprocess.DEVNULL, capture_output=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout)
    except FileNotFoundError:
        return False, f"{launcher_argv(launcher)[0]} not found"
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    tail = [ln for ln in (r.stderr or r.stdout or "").splitlines() if ln.strip()]
    return False, tail[-1].strip() if tail else f"exit {r.returncode}"


def choose_launcher(venv=None, which=None, starts=None, executable=None,
                    script=None):
    """(launcher, portable, why). Portable — a bare `python` / `python3` that
    really imports pythia, outside a virtual environment — goes into the
    files the team shares. Anything else is this machine's own: the absolute
    interpreter (or, for a clone, the interpreter plus this script), and `why`
    says what ruled the portable spelling out."""
    venv = in_virtualenv() if venv is None else venv
    which = which or path_lookup
    starts = starts or (lambda launcher: launcher_starts(launcher)[0])
    exe = pathlib.Path(executable or sys.executable)
    script = script or pathlib.Path(__file__).resolve().as_posix()
    if venv:
        why = (f"pythia runs from the virtual environment {exe.parent.parent.as_posix()}"
               " — `python` finds it only while that environment is active, "
               "and Claude Code started from a desktop app or IDE does not "
               "activate it")
    else:
        names = list(dict.fromkeys(["python", "python3", exe.stem or "python3"]))
        for name in names:
            if which(name) and starts(f"{name} -m pythia"):
                return f"{name} -m pythia", True, ""
        found = which("python")
        why = (f"`python` on PATH ({found}) cannot import pythia" if found else
               "no `python` on PATH can import pythia")
    absolute = f'"{exe.as_posix()}" -m pythia'
    if starts(absolute):
        return absolute, False, why
    return f'"{exe.as_posix()}" "{script}"', False, why


def with_launcher(obj, launcher):
    """A hook entry with its `python -m pythia` spelled as `launcher`."""
    text = json.dumps(obj).replace('"python -m pythia ',
                                   '"' + json.dumps(launcher)[1:-1] + " ")
    return json.loads(text)


def mcp_entry(launcher):
    argv = launcher_argv(launcher)
    return {"command": argv[0], "args": argv[1:] + ["mcp"]}


def mcp_launcher(entry):
    """The launcher an MCP server entry starts pythia with, or None."""
    if not isinstance(entry, dict):
        return None
    args = [str(a) for a in entry.get("args") or []]
    if not args or args[-1] != "mcp":
        return None
    return launcher_text([str(entry.get("command", ""))] + args[:-1])


def local_mcp_entry(project_root, home=None):
    """Claude Code's local-scope `pythia` server for this project (it lives
    in ~/.claude.json, keyed by the project path), or None. Read only."""
    path = pathlib.Path(home or pathlib.Path.home()) / ".claude.json"
    projects = (_read_json(path) or {}).get("projects")
    if not isinstance(projects, dict):
        return None
    root = pathlib.Path(project_root)
    for key in dict.fromkeys([str(root), str(root.resolve()), root.as_posix(),
                              root.resolve().as_posix()]):
        servers = (projects.get(key) or {}).get("mcpServers") or {}
        if isinstance(servers, dict) and "pythia" in servers:
            return servers["pythia"]
    return None


def register_local_mcp(project_root, launcher, which=None, run=None, home=None):
    """Register the MCP approver in Claude Code's local scope — this machine,
    this project, never the shared .mcp.json; a local entry overrides the
    project's. `claude mcp add --scope local` is the supported way in.
    Returns (action, command): added | updated | present | manual | failed;
    on manual and failed the developer runs `command` themselves."""
    import shutil
    import subprocess
    which = which or shutil.which
    run = run or subprocess.run
    want = launcher_argv(launcher) + ["mcp"]
    shown = "claude mcp add --scope local pythia -- " + " ".join(
        f'"{w}"' if re.search(r"\s", w) else w for w in want)
    have = local_mcp_entry(project_root, home)
    if have is not None and [have.get("command")] + list(have.get("args") or []) == want:
        return "present", shown
    claude = which("claude")
    if not claude:
        return "manual", shown
    opts = dict(cwd=str(project_root), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=60)
    old = [str(w) for w in [have.get("command")] + list(have.get("args") or [])] \
        if have is not None else None
    try:
        if old:
            run([claude, "mcp", "remove", "--scope", "local", "pythia"], **opts)
        r = run([claude, "mcp", "add", "--scope", "local", "pythia", "--", *want],
                **opts)
        if r.returncode != 0 and old:       # the old entry, not nothing
            run([claude, "mcp", "add", "--scope", "local", "pythia", "--", *old],
                **opts)
    except (OSError, subprocess.SubprocessError):
        return "failed", shown
    if r.returncode != 0:
        return "failed", shown
    return ("updated" if have is not None else "added"), shown


def launcher_verdict(launcher, which=None, env=None, starts=None, shared=False):
    """(status, code, detail) for one launcher found in the harness files —
    what doctor knows before a word is chosen. code: not-plain | shared-path |
    not-on-path | cannot-start | venv-only | ok; detail: the reason, or the
    active environment. A bare name is resolved on this PATH and run; a path
    (an interpreter's, or a pythia.py's) is run only from a machine's own
    files — in a shared file it is the repository's to choose, so it is
    named, never executed."""
    which = which or path_lookup
    env = os.environ if env is None else env
    starts = starts or launcher_starts
    argv = plain_launcher(launcher)
    if argv is None:
        return "warn", "not-plain", ""
    bare = not re.search(r"[/\\]", argv[0])
    if shared and not (bare and argv[1:] == ["-m", "pythia"]):
        return "warn", "shared-path", ""
    resolved = which(argv[0]) if bare else argv[0]
    if bare and not resolved:
        return "fail", "not-on-path", f"`{argv[0]}` is not on PATH"
    ok, why = starts(launcher)
    if not ok:
        return "fail", "cannot-start", why
    venv = env.get("VIRTUAL_ENV") or env.get("CONDA_PREFIX")
    if bare and venv and os.path.normcase(os.path.abspath(resolved)).startswith(
            os.path.normcase(os.path.abspath(venv)) + os.sep):
        return "warn", "venv-only", venv
    return "ok", "ok", ""


def launcher_message(launcher, code, detail, broken=None):
    """The words for a verdict. `broken` replaces what follows a launcher
    that cannot start — what it leaves off, and the fix — when the caller
    knows more than the launcher does (which file it is in, what else on
    this machine serves the same hooks)."""
    if code == "not-plain":
        return (f"`{launcher}` is not a plain launcher (an interpreter plus -m "
                "pythia, or an absolute pythia.py path) — doctor does not run it")
    if code == "shared-path":
        return (f"`{launcher}` is a path on one machine, in a file the team "
                "shares — teammates' hooks will not find it, and doctor does "
                f"not run it; `{invocation()} install` keeps a machine's own "
                "launcher in .claude/settings.local.json")
    if code in ("not-on-path", "cannot-start"):
        return (f"`{launcher}` cannot start pythia here ({detail}) — " + (
            broken or "the guard and the approval doors it serves are off; "
            f"re-run `{invocation()} install`, which picks an interpreter that "
            "can"))
    if code == "venv-only":
        return (f"`{launcher}` finds pythia only while the virtual environment "
                f"{detail} is active — Claude Code or Codex started from a "
                f"desktop app or IDE will not; re-run `{invocation()} install`, "
                "which pins this interpreter in .claude/settings.local.json")
    return f"`{launcher}` starts pythia"


def launcher_health(launcher, cwd=None, which=None, env=None, starts=None,
                    shared=False, broken=None):
    """(status, message) for one launcher — launcher_verdict in words."""
    st, code, detail = launcher_verdict(launcher, which=which, env=env,
                                        starts=starts, shared=shared)
    return st, launcher_message(launcher, code, detail, broken)


def exclude_local_settings(project_root):
    """Keep .claude/settings.local.json out of git: it holds this machine's
    interpreter path. Appended to .git/info/exclude (local, never shared) when
    neither that nor .gitignore names it. Returns the file written, or None."""
    root = pathlib.Path(project_root)
    common = git_common_dir(root)
    if common is None:
        return None
    line = ".claude/settings.local.json"
    exclude = common / "info" / "exclude"
    for f in (root / ".gitignore", exclude):
        if f.is_file() and any(ln.strip().lstrip("/") == line for ln in
                               f.read_text(encoding="utf-8", errors="replace")
                               .splitlines()):
            return None
    data = exclude.read_bytes() if exclude.is_file() else b""
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "ab") as f:          # their bytes stay theirs
        f.write((b"\n" if data and not data.endswith(b"\n") else b"")
                + line.encode("utf-8") + b"\n")
    return exclude


SECRET_REL = f"{CONFIG_DIR}/{CONFIG_NAME}"


def git_ignore_source(root, rel):
    """How git treats `rel` under root: ("shared", source) when a .gitignore
    in the repository ignores it — every clone; ("local", source) when only
    this machine does (.git/info/exclude, a personal excludes file);
    ("none", "") when `git add -A` would take it. None outside a repository
    or without git. The index is not consulted: tracked is git_tracks()."""
    import subprocess
    git = path_lookup("git")
    if not git:
        return None
    try:
        r = subprocess.run([git, "-C", str(root), "check-ignore", "-v",
                            "--no-index", "--", rel], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode not in (0, 1):
        return None
    # <source>:<line>:<pattern>TAB<path>; a match on "!pattern" un-ignores it
    m = re.match(r"(.+?):(\d+):(.*?)\t", r.stdout)
    if r.returncode == 1 or not m or m.group(3).startswith("!"):
        return "none", ""
    src = m.group(1)
    shared = (not _is_abs_path(src)
              and os.path.basename(src.replace("\\", "/")) == ".gitignore")
    return ("shared" if shared else "local"), src


def _gitignore_covers(path, rel):
    """Read as text where git cannot answer: does the last rule in `path`
    naming rel (or its folder) ignore it?"""
    if not path.is_file():
        return False
    folder = rel.split("/")[0]
    names = {rel, folder, folder + "/", folder + "/*", folder + "/**"}
    covered = False
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        neg = s.startswith("!")
        body = (s[1:] if neg else s).lstrip("/")
        if body in names:
            covered = not neg
    return covered


def ignore_connections(project_root):
    """Keep .pythia/connections.json — the passwords — out of every clone:
    append it to the project's .gitignore unless a rule there already ignores
    it. A rule only this machine has is not enough, and outside git the line
    is written anyway, so a later `git init` starts safe. Returns the
    .gitignore written, or None."""
    root = pathlib.Path(project_root)
    gi = root / ".gitignore"
    state = git_ignore_source(root, SECRET_REL)
    if state is not None and state[0] == "shared":
        return None
    if _gitignore_covers(gi, SECRET_REL):
        return None        # written already; a rule deeper down overrides it
    data = gi.read_bytes() if gi.is_file() else b""
    with open(gi, "ab") as f:               # their bytes stay theirs
        f.write((b"\n" if data and not data.endswith(b"\n") else b"")
                + b"# pythia: database passwords, never committed\n"
                + SECRET_REL.encode("utf-8") + b"\n")
    return gi


def report_connections_ignore(project_root):
    root = pathlib.Path(project_root)
    try:
        gi = ignore_connections(root)
    except OSError as e:            # a safeguard, never a reason to abort
        gi = None
        print(f"WARNING: could not write {root / '.gitignore'} ({e}) — "
              f"add {SECRET_REL} to it by hand; the file holds passwords.")
    if gi is not None:
        print(f"Added {SECRET_REL} to {gi} — it holds passwords; commit the "
              ".gitignore so every clone keeps it out")
    if git_tracks(root, SECRET_REL):
        print(f"WARNING: git already tracks {SECRET_REL}, so its passwords are "
              f"in the repository. Run `git rm --cached {SECRET_REL}`, commit, "
              "and change those passwords — the history keeps the old ones.")


def credentials_check(root):
    """doctor's row for the password file: fail when git tracks it, warn
    when git would commit it, ok when a shared rule keeps it out."""
    state = git_ignore_source(root, SECRET_REL)
    if state is None:
        return ("info", "credentials", f"{SECRET_REL} not checked — no git "
                "repository here (or git is not on PATH)")
    if git_tracks(root, SECRET_REL):
        return ("fail", "credentials", f"git tracks {SECRET_REL} — its "
                f"passwords are in the repository; `git rm --cached "
                f"{SECRET_REL}`, commit, then change those passwords (the "
                "history keeps the old ones)")
    kind, src = state
    fix = f"{invocation()} install adds it to .gitignore"
    if kind == "shared":
        return ("ok", "credentials", f"{SECRET_REL} is git-ignored ({src})")
    if kind == "local":
        return ("warn", "credentials", f"{SECRET_REL} is ignored only on this "
                f"machine ({src}) — a teammate's clone would commit theirs; "
                + fix)
    return ("warn", "credentials", f"{SECRET_REL} is not git-ignored — one "
            f"`git add -A` commits the passwords; " + fix)


def git_common_dir(root):
    """The directory holding info/exclude: .git itself, or — in a worktree or
    a submodule, where .git is a file — the gitdir it names (a worktree's
    commondir). None outside git."""
    git = pathlib.Path(root) / ".git"
    if git.is_dir():
        return git
    if not git.is_file():
        return None
    m = re.match(r"gitdir:\s*(.+)", git.read_text(encoding="utf-8",
                                                  errors="replace").strip())
    if not m:
        return None
    norm = lambda base, p: pathlib.Path(os.path.normpath(       # noqa: E731
        p if _is_abs_path(str(p)) else pathlib.Path(base) / p))
    gitdir = norm(root, m.group(1).strip())
    cd = gitdir / "commondir"
    if cd.is_file():
        return norm(gitdir, cd.read_text(encoding="utf-8",
                                         errors="replace").strip())
    return gitdir


def merge_hooks(hooks, wanted, launcher=None, respell=False):
    """Merge wanted {event: [entries]} into a hooks object in place. An entry
    counts as present when an existing entry with the same matcher runs the
    same pythia subcommand, in any spelling. Legacy commands are rewritten in
    place. New entries are spelled with `launcher` when given; `respell`
    (this machine's own file) also rewrites the launcher of pythia entries
    already there — a moved venv leaves a path that no longer starts. Returns
    the event names changed."""
    changed = []
    for event, entries in wanted.items():
        present = hooks.setdefault(event, [])
        upgraded = False
        for e in present:
            for h in e.get("hooks", []) if isinstance(e, dict) else []:
                if not isinstance(h, dict):
                    continue
                for old, new in LEGACY_HOOK_COMMANDS.items():
                    if _pythia_mark(h.get("command", "")) == old:
                        h["command"] = h["command"].replace(old, new)
                        upgraded = True
                cmd = str(h.get("command", ""))
                mark = _pythia_mark(cmd)
                if respell and launcher and mark and \
                        cmd[:cmd.index(mark)].strip() != launcher:
                    h["command"] = f"{launcher} {cmd[cmd.index(mark):]}"
                    upgraded = True
        have = {(e.get("matcher"), _pythia_mark(h.get("command", "")))
                for e in present if isinstance(e, dict)
                for h in e.get("hooks", []) if isinstance(h, dict)}
        added = False
        for entry in entries:
            key = (entry.get("matcher"), _hook_mark(entry["hooks"][0]["command"]))
            if key not in have:
                present.append(with_launcher(entry, launcher) if launcher
                               else local_interpreter(entry))      # a copy
                have.add(key)
                added = True
        if added or upgraded:
            changed.append(event)
    return changed


# Codex loads hooks from <repo>/.codex/hooks.json once the layer is trusted
# (/hooks). Only the session-start guide is wired — Codex has no answer-reading
# PostToolUse and no question tool, so the chat approve-mint cannot port; the
# developer approves in the terminal. Same shape and merge discipline as the
# Claude hooks.
CODEX_HOOKS = {
    "SessionStart": [{"hooks": [
        {"type": "command", "command": "python -m pythia hook session-start"}]}],
}


def merge_codex_hooks(path, events=None):
    """Merge pythia's Codex hooks into a hooks.json ({"hooks": {...}}). Every
    other key survives, a hook already present in any spelling is recognised by
    what it runs, and a file that will not parse is left byte-identical.
    Returns (path, added|None)."""
    path = pathlib.Path(path)
    try:
        s = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        hooks = s.setdefault("hooks", {})
        wanted = {e: CODEX_HOOKS[e] for e in (events or CODEX_HOOKS)}
        added = [f"hooks.{e}" for e in merge_hooks(hooks, wanted)]
    except (ValueError, TypeError, AttributeError):
        return path, None
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return path, added


# The MCP approver: a write-free stdio server whose one tool, pythia_approve,
# mints the same grant `approve` mints — gated by an MCP elicitation the
# developer answers in Codex. Writes never go through here; apply still
# verifies the grant. Hand-rolled JSON-RPC (stdlib only).
MCP_TOOL = "pythia_approve"
MCP_PROTOCOL = "2025-06-18"
MCP_ELICIT_METHOD = "elicitation/create"   # the one spelling to adjust if a
#                                            live Codex disagrees (isolated)
_MCP_TOOL_DEF = {
    "name": MCP_TOOL,
    # Claude Code >= 2.1.214 prompts for a tool marked like this on EVERY
    # call, in every permission mode but dontAsk, and no allow rule skips it.
    # That is the property AskUserQuestion was supposed to have and, in some
    # setups, did not; the elicitation below is still the actual decision.
    "_meta": {"anthropic/requiresUserInteraction": True},
    "description": ("Ask the developer to approve something pythia printed a "
                    "token for: an apply preview (6 hex), an autopilot lease "
                    "request (8 hex) or a deploy batch (10 hex). Shows pythia's "
                    "own approval card and records the approval only if the "
                    "developer picks Approve. Then run the command the result "
                    "names yourself (`apply <file> --confirm <token>`, "
                    "`deploy <files> --confirm <token>`, or plain `apply` "
                    "under an active autopilot lease)."),
    "inputSchema": {"type": "object",
                    "properties": {"token": {"type": "string"}},
                    "required": ["token"]},
}


def _rpc_result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def mcp_handle(msg, state):
    """Dispatch one incoming JSON-RPC message that is NOT a tools/call (those
    need the elicitation round-trip and are handled in the loop). Returns
    (outgoing_messages, done). A notification (no id) is answered with []."""
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion") or MCP_PROTOCOL
        return [_rpc_result(mid, {
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pythia",
                           "version": getattr(sys.modules[__name__],
                                              "__version__", "0")}})], False
    if method == "notifications/initialized":
        return [], False
    if method == "tools/list":
        return [_rpc_result(mid, {"tools": [_MCP_TOOL_DEF]})], False
    if mid is None:
        return [], False                       # any other notification
    return [_rpc_error(mid, -32601, f"method not found: {method}")], False


def _mcp_text(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# no one reads a card and answers it faster than this: a quicker non-accept is
# the client answering for nobody (a host with no elicitation UI declines at once)
MCP_HUMAN_FLOOR_S = 1.5


def next_door_after_mcp(token):
    """Where the agent goes, without asking, when the MCP prompt fails."""
    return (f"Next door, now — no need to ask the developer first: if you have "
            f"not yet asked with AskUserQuestion for this token, do it — its "
            f"text exactly the output of `{invocation()} approve --card "
            f"{token}`, options Approve / Reject; otherwise, or with no "
            f"AskUserQuestion (Codex), relay `{invocation()} approve {token}` "
            "for the developer's terminal. Do not call pythia_approve again "
            "this session.")


def mcp_approve_decision(root, token, elicit, clock=None):
    """Mint on a developer's Approve, delivered through an MCP elicitation.

    Transport-free: `elicit(message, schema) -> (action, content)` performs the
    round-trip (a fake in tests). The card is pythia's own, built from the
    journal — so there is nothing for the agent to paraphrase and no verbatim
    check to run. Fail-closed everywhere: the only path that mints is an
    explicit accept whose decision is exactly Approve. A decline or cancel
    back sooner than anyone could read the card is the client answering for
    nobody, said as such, so the agent moves to the next door instead of
    asking the developer what to change."""
    import time
    clock = clock or time.monotonic
    token = str(token or "").strip().lower()
    try:
        _, meta, body = approval_card(root, token)   # refuses unknown/applied
    except SystemExit as e:
        return _mcp_text(str(e), is_error=True)
    message = f"{invocation()} approve {token}\n" + "\n".join(body)
    schema = {"type": "object",
              "properties": {"decision": {"type": "string",
                                          "enum": ["Approve", "Reject"]}},
              "required": ["decision"]}
    started = clock()
    try:
        action, content = elicit(message, schema)
    except Exception as e:                        # noqa: BLE001 — fail closed
        return _mcp_text(f"No grant minted for {token}: the approval prompt "
                         f"could not be shown ({e}). "
                         + next_door_after_mcp(token), is_error=True)
    took = clock() - started
    if action != "accept" and took < MCP_HUMAN_FLOOR_S:
        return _mcp_text(f"No grant minted for {token}: the prompt came back "
                         f"{action!r} after {round(took * 1000)} ms — before "
                         "anyone could read the card, so the client did not "
                         "show it (this client shows no MCP prompts). That is "
                         "not the developer's answer. "
                         + next_door_after_mcp(token), is_error=True)
    if action != "accept" or (content or {}).get("decision") != "Approve":
        return _mcp_text(f"No grant minted for {token}: the developer did not "
                         "approve (answered "
                         f"{(content or {}).get('decision') or action!r}). "
                         "Do not apply; ask what should change.", is_error=True)
    msg = grant_approval(root, token, meta, "mcp")
    return _mcp_text(f"Approved {token} — {msg} Run it YOURSELF now — do not "
                     "hand the developer a command to type.")


def mcp_serve(reader, writer, root):
    """The stdio loop. Newline-delimited JSON-RPC in and out. A tools/call for
    pythia_approve runs the elicitation round-trip inline: send
    elicitation/create, then read until the matching-id response. One call in
    flight at a time (Codex calls tools serially in a turn)."""
    counter = [1000]

    def send(msg):
        writer.write(json.dumps(msg) + "\n")
        writer.flush()

    def read_msg():
        line = reader.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except ValueError:
            return {}                       # skip a malformed line

    def elicit(message, schema):
        counter[0] += 1
        eid = counter[0]
        send({"jsonrpc": "2.0", "id": eid, "method": MCP_ELICIT_METHOD,
              "params": {"message": message, "requestedSchema": schema}})
        while True:
            m = read_msg()
            if m is None:
                raise RuntimeError("client closed before answering the elicitation")
            if m.get("id") == eid and ("result" in m or "error" in m):
                if "error" in m:
                    raise RuntimeError(str(m["error"]))
                r = m["result"] or {}
                return r.get("action"), r.get("content") or {}
            # ignore anything else that arrives mid-elicitation

    while True:
        msg = read_msg()
        if msg is None:
            return
        if msg.get("method") == "tools/call":
            mid = msg.get("id")
            params = msg.get("params") or {}
            if params.get("name") != MCP_TOOL:
                send(_rpc_error(mid, -32601, f"no such tool: {params.get('name')}"))
                continue
            token = (params.get("arguments") or {}).get("token", "")
            result = mcp_approve_decision(root, token, elicit)
            send(_rpc_result(mid, result))
            continue
        out, _ = mcp_handle(msg, {})
        for m in out:
            send(m)


def cmd_mcp(conn, schema, ns):
    """Run the MCP approver on stdio. Launched by Codex per its
    [mcp_servers.pythia] config; lives for the session. Touches no database."""
    mcp_serve(sys.stdin, sys.stdout, ns.project_root)


# Codex config is TOML, and the stdlib cannot parse or write it on 3.9
# (tomllib is read-only, 3.11+). So these merges never parse TOML: they append
# a marker-delimited block when the target tables are absent, and refuse
# (writing nothing) when a table already exists — the installer then prints
# what to add. ponytail: literal-text collision check, not a TOML engine; a
# table header inside a string/comment is not worth a parser here.
CODEX_MCP_BEGIN = "# pythia:begin (managed by `pythia install`)"
CODEX_MCP_END = "# pythia:end"
# Only the server roster — nothing else. An earlier attempt also wrote an
# [approval_policy.granular] table, but that table requires several fields
# (sandbox_approval, rules, ...) and an incomplete one broke Codex v0.154 at
# config-load time. Elicitations surface under the default interactive
# approval policy without any of that, and the developer's approval policy is
# theirs, not ours to set.
CODEX_MCP_BLOCK = (f"{CODEX_MCP_BEGIN}\n"
                   "[mcp_servers.pythia]\n"
                   'command = "python"\n'
                   'args = ["-m", "pythia", "mcp"]\n'
                   f"{CODEX_MCP_END}")


def merge_codex_mcp_config(path):
    """Append the MCP server registration to <project>/.codex/config.toml.
    Returns (path, action): 'created' | 'appended' | 'present' | 'conflict'.
    Refuses (writes nothing) if [mcp_servers.pythia] already exists without our
    marker — the stdlib cannot safely merge TOML, so a collision is reported,
    never overwritten."""
    path = pathlib.Path(path)
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    if old is not None and CODEX_MCP_BEGIN in old:
        return path, "present"
    if old is not None and "[mcp_servers.pythia]" in old:
        return path, "conflict"
    if old is None:
        new, action = CODEX_MCP_BLOCK + "\n", "created"
    else:
        tail = "" if old.endswith("\n\n") else "\n" if old.endswith("\n") else "\n\n"
        new, action = old + tail + CODEX_MCP_BLOCK + "\n", "appended"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return path, action


def install_claude_hooks(base_dir, events=None, trust_mcp=False,
                         launcher=None, local=False, respell=False):
    """Merge pythia's hooks and the deny rule into <base>/.claude/settings.json
    — the project's, or the home directory's for -g. The file is Claude
    Code's, so every other key survives and a pythia hook already there in
    any spelling counts as present. Returns (path, added): the pieces
    written, [] when all were there, None when the file could not be parsed
    and was therefore left alone. `events` narrows which hooks: -g writes no
    SessionStart, since ~/.claude/settings.json applies to every project and
    the session guide would be noise outside Oracle work. `trust_mcp` also
    enables the project's pythia MCP server (install registered it; asking
    the developer to trust their own install a second time is a prompt that
    carries no decision). `launcher` spells the commands; `local` writes
    .claude/settings.local.json instead — this machine's file, for a launcher
    that is a path on this machine. In this machine's files (`local`, or
    `respell` for ~/.claude) pythia's existing entries take the launcher too."""
    name = "settings.local.json" if local else "settings.json"
    path = pathlib.Path(base_dir) / ".claude" / name
    try:
        s = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        hooks = s.setdefault("hooks", {})
        deny = s.setdefault("permissions", {}).setdefault("deny", [])
        wanted = {e: CLAUDE_HOOKS[e] for e in (events or CLAUDE_HOOKS)}
        added = [f"hooks.{e}" for e in
                 merge_hooks(hooks, wanted, launcher, respell=respell or local)]
        spelled = [f"Bash({launcher} approve --hook*)"] if launcher else []
        missing = [d for d in dict.fromkeys(CLAUDE_DENY + spelled) if d not in deny]
        if missing:
            deny.extend(missing)
            added.append("permissions.deny")
        if trust_mcp and not local:
            enabled = s.setdefault("enabledMcpjsonServers", [])
            if "pythia" not in enabled:
                enabled.append("pythia")
                added.append("enabledMcpjsonServers")
    except (ValueError, TypeError, AttributeError):
        return path, None
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return path, added


def install_claude_mcp(project_root, launcher=None):
    """Register the write-free MCP approver in the project's .mcp.json, where
    Claude Code reads project MCP servers. Merge-only: other servers survive,
    an existing pythia entry is the developer's and stays, a file that will
    not parse is left alone. The file is shared: `launcher` is a portable one
    (register_local_mcp takes a machine's own). Returns (path,
    'created'|'added'|'present'|None)."""
    path = pathlib.Path(project_root) / ".mcp.json"
    try:
        s = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        servers = s.setdefault("mcpServers", {})
        if "pythia" in servers:
            return path, "present"
        servers["pythia"] = (mcp_entry(launcher) if launcher
                             else local_interpreter(MCP_SERVER_ENTRY))
    except (ValueError, TypeError, AttributeError):
        return path, None
    action = "added" if path.is_file() else "created"
    path.write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path, action


def report_local_launcher(project_root, launcher, why):
    """Project scope, when no portable spelling starts pythia on this machine:
    the hooks go to .claude/settings.local.json and the MCP approver to
    Claude Code's local scope — never into the files the team shares, where
    this machine's path would break every teammate's hooks."""
    root = pathlib.Path(project_root)
    print(f"\nThis machine needs its own launcher: {why}.")
    print(f"Claude Code's hooks and MCP approver start pythia with {launcher},"
          "\nwritten to this machine's files only.")
    if git_tracks(root, ".claude/settings.local.json"):
        path, added = root / ".claude" / "settings.local.json", "tracked"
    else:
        path, added = install_claude_hooks(root, launcher=launcher, local=True)
    if added == "tracked":
        print(f"! {path} is tracked by git, so the team shares it: this "
              "machine's interpreter is not written there. Untrack it (`git rm "
              "--cached .claude/settings.local.json`) and re-run "
              f"{invocation()} install.")
    elif added is None:
        print(f"! {path} is not valid JSON, so it was left untouched. Fix it "
              f"and re-run {invocation()} install.")
    elif added:
        print(f"Wired Claude Code hooks into {path}: {', '.join(added)}.")
        print("Restart the Claude Code session for them to load.")
    else:
        print(f"Claude Code hooks already in {path}.")
    ex = exclude_local_settings(root)
    if ex:
        print(f"Kept it out of git: .claude/settings.local.json added to {ex}.")
    action, shown = register_local_mcp(root, launcher)
    if action in ("added", "updated"):
        print("Registered the pythia approver (MCP) in Claude Code's local "
              "scope for this project — the approval door that prompts in "
              "every permission mode.")
    elif action == "present":
        print("pythia approver (MCP) already registered in Claude Code's local "
              "scope.")
    else:
        why_not = ("the `claude` CLI is not on PATH" if action == "manual"
                   else "`claude mcp add` failed")
        print(f"! The MCP approver is not registered: {why_not}. Run this once "
              f"in the project:\n  {shown}")
    shared = _read_json(root / ".claude" / "settings.json") or {}
    marks = [_hook_mark(h.get("command", "")) for ev in
             (shared.get("hooks") or {}).values() if isinstance(ev, list)
             for e in ev if isinstance(e, dict)
             for h in e.get("hooks", []) if isinstance(h, dict)]
    if any(marks):
        print("! .claude/settings.json also carries pythia hooks. Teammates may "
              "rely on them; on this machine they run too, and each one that "
              "cannot start shows a hook error. Keep them for the team or "
              "remove them — pythia leaves shared files alone.")


def launcher_for(ns):
    """choose_launcher() once per install run: it starts interpreters."""
    if getattr(ns, "_launcher", None) is None:
        ns._launcher = choose_launcher()
    return ns._launcher


def report_claude_hooks(base_dir, ns, events=None):
    """install's last step, in both scopes: wire the hooks and say what
    happened. --no-hooks leaves Claude Code's file alone."""
    if getattr(ns, "no_hooks", False):
        return
    project = events is None
    launcher, portable, why = launcher_for(ns)
    if project and not portable:
        return report_local_launcher(base_dir, launcher, why)
    if not portable:                 # -g: ~/.claude is this machine's already
        print(f"\nThis machine needs its own launcher: {why}.\nThe hooks in "
              f"{pathlib.Path(base_dir) / '.claude' / 'settings.json'} start "
              f"pythia with {launcher}.")
    if project:
        mpath, maction = install_claude_mcp(base_dir, launcher=launcher)
        if maction in ("created", "added"):
            print(f"\nRegistered the pythia approver (MCP) in {mpath} — the "
                  "approval door that prompts in every permission mode.")
        elif maction is None:
            print(f"\n! {mpath} is not valid JSON, so the MCP approver was not "
                  "registered; AskUserQuestion and the console still work.")
    path, added = install_claude_hooks(base_dir, events, trust_mcp=project,
                                       launcher=launcher, respell=not project)
    if added is None:
        print(f"\n! {path} is not valid JSON, so it was left untouched. Fix "
              f"it and re-run {invocation()} install, or paste the hooks "
              "block from GUIDE.md section 11.")
    elif added:
        print()
        print(f"Wired Claude Code hooks into {path}: {', '.join(added)}.")
        print("Restart the Claude Code session for them to load.")
    else:
        print(f"\nClaude Code hooks already in {path}.")


def report_claude_md(project_root):
    """Project scope: write the harness into <project>/CLAUDE.md, the file
    Claude Code loads every session — so the rules hold even with --no-hooks,
    before a restart, or while the hooks are untrusted. Not on -g: a global
    CLAUDE.md would put pythia into every session on the machine."""
    path, action = merge_claude_md(pathlib.Path(project_root) / "CLAUDE.md")
    verb = {"created": "Wrote the pythia harness into",
            "added": "Added the pythia block to",
            "updated": "Updated the pythia block in",
            "unchanged": "pythia harness already current in"}[action]
    print(f"\n{verb} {path} (Claude Code loads it every session).")


def report_codex_agents_md(project_root):
    """Project scope: write the harness into <project>/AGENTS.md, the file
    Codex loads every session. Not done on -g — a global AGENTS.md is noise in
    every non-Oracle session, as the Claude guide is per-project."""
    path, action = merge_agents_md(pathlib.Path(project_root) / "AGENTS.md")
    verb = {"created": "Wrote the pythia harness into",
            "added": "Added the pythia block to",
            "updated": "Updated the pythia block in",
            "unchanged": "pythia harness already current in"}[action]
    print(f"\n{verb} {path} (Codex and other AGENTS.md agents load it each session).")


def report_codex_hooks(project_root, ns):
    """Project scope: the Codex session-start guide hook. --no-hooks skips it,
    as with the Claude hooks. AGENTS.md already carries the guide, so this is
    reinforcement for hook-driven Codex users."""
    if getattr(ns, "no_hooks", False):
        return
    path, added = merge_codex_hooks(pathlib.Path(project_root) / ".codex" / "hooks.json")
    if added is None:
        print(f"\n! {path} is not valid JSON, so it was left untouched.")
    elif added:
        print(f"\nWired the Codex session-start hook into {path}.")
        print("In Codex, run /hooks once to trust this project's .codex layer.")
    else:
        print(f"\nCodex session-start hook already in {path}.")


def report_codex_mcp(project_root, ns):
    """Project scope: register the write-free MCP approver in Codex's config.
    --no-hooks skips it, as with the other Codex wiring. Needs Codex >= v0.120
    for elicitation; older Codex ignores the server and the console approve
    path still works."""
    if getattr(ns, "no_hooks", False):
        return
    cfg_path, cfg = merge_codex_mcp_config(pathlib.Path(project_root)
                                           / ".codex" / "config.toml")
    if cfg in ("created", "appended"):
        print(f"\nRegistered the pythia MCP approver in {cfg_path} "
              "(Codex >= v0.120; run /mcp to trust it).")
    elif cfg == "conflict":
        print(f"\n! {cfg_path} already defines [mcp_servers.pythia]; add this "
              "yourself:\n" + CODEX_MCP_BLOCK)
    else:
        print(f"\npythia MCP approver already registered in {cfg_path}.")
    launcher, portable, why = launcher_for(ns)
    if not portable:
        print(f"! Codex: .codex/ starts pythia with `python -m pythia`, which "
              f"cannot start it on this machine ({why}). The .codex/ files are "
              f"shared and stay portable; point your own ~/.codex/config.toml "
              f"[mcp_servers.pythia] at {launcher} instead.")
    # The one bypass of the elicitation is Codex auto-approving it under the
    # danger-full-access sandbox. pythia does not write requirements.toml to
    # forbid that — it is enterprise-managed config, and the schema is
    # version-fragile — so it warns instead.
    print("Do not run Codex under `danger-full-access` with pythia: that "
          "sandbox auto-approves elicitations, bypassing your Approve.")


def cmd_install(conn, schema, ns):
    import shutil
    en = getattr(ns, "color", False)
    if en:
        sys.stdout.write(banner(en))
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if ns.glob:
        code = run_skills_add(ns.source, interactive, glob=True)
        if code is None:
            for tgt in copy_bundled_skills(pathlib.Path.home()):
                print(f"Copied the bundled skill pack into {tgt}")
        elif code:
            sys.exit(code)
        else:
            clean_legacy_skills(pathlib.Path.home())
        # machine-wide: the approve and guard hooks are silent without a
        # pythia command; the session guide would be noise in every other
        # project
        report_claude_hooks(pathlib.Path.home(), ns,
                            events=("PreToolUse", "PostToolUse"))
        print("\nGlobal skills serve every project — per-project installs "
              "will now skip the skills step automatically.")
        return
    path, created = scaffold_config(ns.project_root)
    print(f"{'Created' if created else 'Kept existing'} {path}")
    report_connections_ignore(ns.project_root)
    if global_pack_present():
        print("Skill pack found in the global install — it already serves "
              "this project,\nso no project copy is made (a second copy "
              f"doubles every menu entry).\nRefresh it with: {invocation()} "
              "install -g")
        clean_legacy_skills(ns.project_root)
    else:
        code = run_skills_add(ns.source, interactive)
        if code is None:
            for tgt in copy_bundled_skills(ns.project_root):
                print(f"Copied the bundled skill pack into {tgt}")
            print("(npx not found — with Node.js, `npx skills add` reaches "
                  "77 agents with symlinked updates.)")
        elif code:
            sys.exit(code)
        else:
            clean_legacy_skills(ns.project_root)
    report_claude_hooks(ns.project_root, ns)
    report_claude_md(ns.project_root)
    report_codex_agents_md(ns.project_root)
    report_codex_hooks(ns.project_root, ns)
    report_codex_mcp(ns.project_root, ns)
    print(f"\nNext: fill in {path}")
    print(f"Then: {invocation()} check")
    scripts_dir = installed_scripts_dir()
    hint = entry_point_hint(shutil.which("pythia"), scripts_dir,
                            in_stored_path=stored_path_has(scripts_dir))
    if hint:
        print()
        print(hint)
        offer_path_fix(scripts_dir, interactive,
                       getattr(ns, "add_to_path", False),
                       skip=getattr(ns, "no_path", False))


# --- scripts: splitting a multi-statement file (tests/test_phase6.py) --------
#
# apply takes one statement per file, and that rule stays: one preview, one
# token, one journal entry per statement. A release script is many of them, so
# deploy splits it here — with SQL*Plus semantics, because that is how every
# .sql file a developer already has was written.

Q_CLOSE = {"[": "]", "{": "}", "(": ")", "<": ">"}

# lines SQL*Plus executes itself — never SQL, so never sent to the database
SQLPLUS_DIRECTIVE_RE = re.compile(
    r"^\s*(set|prompt|spool|rem|remark|show|whenever|exit|quit|define|"
    r"undefine|col|column|ttitle|btitle|break|compute|pause|accept|"
    r"clear|timing|var|variable|print)(\s|$)", re.I)
# ...and the ones that DO something: EXEC runs an anonymous block, HOST an OS
# command, CONNECT another session. Skipping them would report a release as
# done that never ran its migration — so they are refused.
SQLPLUS_REFUSED_RE = re.compile(
    r"^\s*(exec|execute|host|connect|conn|disconnect|!|\$)(\s|$)", re.I)
SQLPLUS_INCLUDE_RE = re.compile(r"^\s*(@@?|start\s)", re.I)

# a PL/SQL unit enters SQL*Plus's block buffer and ends only at a lone "/";
# views and every other statement end at the first ";" outside a literal
PLSQL_BLOCK_RE = re.compile(
    r"^create\s+(?:or\s+replace\s+)?(?:(?:non?)?editionable\s+|(?:no)?force\s+)*"
    r"(procedure|function|package|trigger|type)\b|^(begin|declare)\b",
    re.I | re.S)


def _q_open(text, i):
    """Index just past the delimiter if a q'X / nq'X literal opens at i."""
    c = text[i]
    if i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_$#"):
        return None
    if c in "qQ" and text[i + 1:i + 2] == "'" and i + 2 < len(text):
        return i + 3
    if c in "nN" and text[i + 1:i + 3].lower() == "q'" and i + 3 < len(text):
        return i + 4
    return None


def sql_spans(text, state=None):
    """Literal and comment spans of text: [(kind, start, end)], kind
    'literal' or 'comment'. `state` carries an unfinished construct across
    calls (line-by-line scanning): {"in": None | "'" | "*" | ("q", close)}."""
    state = state if state is not None else {}
    spans, i, n = [], 0, len(text)
    mode = state.get("in")
    if mode:                                  # resume what the last line left open
        if mode == "*":
            j = text.find("*/")
            kind, closed, end = "comment", j >= 0, j + 2
        elif mode == "'":
            j = 0
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            kind, closed, end = "literal", j < n, j + 1
        else:
            j = text.find(mode[1] + "'")
            kind, closed, end = "literal", j >= 0, j + 2
        if not closed:
            spans.append((kind, 0, n))
            return spans                      # still open: state unchanged
        spans.append((kind, 0, end))
        state["in"] = None
        i = end
    while i < n:
        c = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            spans.append(("comment", i, j))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                spans.append(("comment", i, n))
                state["in"] = "*"
                return spans
            spans.append(("comment", i, j + 2))
            i = j + 2
        elif c in "qQnN" and _q_open(text, i):
            k = _q_open(text, i)
            close = Q_CLOSE.get(text[k - 1], text[k - 1])
            j = text.find(close + "'", k)
            if j < 0:
                spans.append(("literal", i, n))
                state["in"] = ("q", close)
                return spans
            spans.append(("literal", i, j + 2))
            i = j + 2
        elif c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                spans.append(("literal", i, n))
                state["in"] = "'"
                return spans
            spans.append(("literal", i, j + 1))
            i = j + 1
        else:
            i += 1
    return spans


def _blank(text, spans):
    out = list(text)
    for _, a, b in spans:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


def mask_line(line, state):
    """One line with literals and comments blanked to spaces, same length,
    carrying lexer state across lines — a literal or a block comment can
    span many. Handles '' and q'[...]'."""
    return _blank(line, sql_spans(line, state))


def mask_sql(text):
    """Literals (including q'..') and comments blanked, length and line
    breaks preserved."""
    return _blank(text, sql_spans(text))


def literal_spans(text):
    """(start, end) of every string literal in text."""
    return [(a, b) for kind, a, b in sql_spans(text) if kind == "literal"]


def split_script(text):
    """Split a SQL*Plus-style script into the statements it would run.

    Returns (statements, skipped): statements are dicts {"line", "text"} with
    1-based start lines; skipped are {"line", "text", "why"} for SQL*Plus
    directives, which run nothing on the database. An @include is refused —
    deploy never follows a path the developer did not list."""
    stmts, skipped = [], []
    buf, bmask, start, block, state = [], [], None, False, {}

    def significant():
        return any(m.strip() for m in bmask)

    def flush():
        nonlocal buf, bmask, start, block
        body = "\n".join(buf).strip()
        if body and "".join(bmask).strip():
            stmts.append({"line": start, "text": body})
        buf, bmask, start, block = [], [], None, False

    for no, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        if not significant() and not state.get("in"):
            if SQLPLUS_INCLUDE_RE.match(line):
                sys.exit(f"line {no}: {line.strip()!r} includes another "
                         "script. deploy does not follow includes — list the "
                         "files in the order to run them instead.")
            if SQLPLUS_REFUSED_RE.match(line):
                sys.exit(f"line {no}: {line.strip()[:60]!r} is a SQL*Plus "
                         "command that runs something (EXEC = an anonymous "
                         "block, HOST = the OS, CONNECT = another session). "
                         "The write path refuses it: make it a named "
                         "procedure the developer calls, or run it by hand.")
            if SQLPLUS_DIRECTIVE_RE.match(line):
                skipped.append({"line": no, "text": line.strip(),
                                "why": "SQL*Plus directive — runs nothing on "
                                       "the database"})
                buf, bmask, start = [], [], None   # keep line numbers exact
                continue
            if line.strip() == "/":
                continue                     # a stray run-buffer slash
            if not buf and not line.strip():
                continue
        was_open = bool(state.get("in"))
        masked = mask_line(line, state)
        lone_slash = masked.strip() == "/" and not was_open
        if not block and not lone_slash:
            head = "\n".join(bmask + [masked]).lstrip()
            block = bool(PLSQL_BLOCK_RE.match(head))
        if block:
            if lone_slash:
                flush()
            else:
                if start is None:
                    start = no
                buf.append(line)
                bmask.append(masked)
            continue
        if lone_slash:
            if significant():
                flush()                      # "/" also runs a SQL buffer
            continue
        while True:                          # SQL: each ';' ends a statement
            k = masked.find(";")
            if k < 0:
                break
            if start is None:
                start = no
            buf.append(line[:k])
            bmask.append(masked[:k])
            flush()
            line, masked = line[k + 1:], masked[k + 1:]
        if line.strip() or buf:
            if start is None and line.strip():
                start = no
            buf.append(line)
            bmask.append(masked)
            if not block:
                head = "\n".join(bmask).lstrip()
                block = bool(PLSQL_BLOCK_RE.match(head))
    flush()
    return stmts, skipped


# --- comments: COMMENT ON TABLE / COLUMN, the group with a real undo ---------

_IDENT = r'(?:"[^"]+"|[A-Za-z][\w$#]*)'
COMMENT_TARGET_RE = re.compile(
    r"^comment\s+on\s+(table|column)\s+(" + _IDENT + r"(?:\s*\.\s*" + _IDENT
    + r"){0,2})\s+is\s+", re.I | re.S)


def _ident_value(tok):
    """The dictionary spelling of an identifier: quoted keeps its case,
    unquoted folds to upper — exactly what Oracle stores."""
    tok = tok.strip()
    return tok[1:-1] if tok.startswith('"') else tok.upper()


def sql_ident(name):
    """Quote an identifier only when Oracle would need it."""
    if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", name):
        return name
    return '"' + name.replace('"', '""') + '"'


def parse_comment_target(sql):
    """(kind, schema|None, table, column|None) of a COMMENT ON statement.
    Strict on purpose: the target decides which comment is snapshotted, so a
    guess here would snapshot the wrong one and lose the only undo."""
    s = skip_leading_noise(sql)
    m = COMMENT_TARGET_RE.match(s)
    if not m:
        sys.exit("Cannot parse the target of this COMMENT ON statement. "
                 "Expected COMMENT ON TABLE t IS '...' or "
                 "COMMENT ON COLUMN t.c IS '...'.")
    kind = m.group(1).lower()
    parts = [_ident_value(p) for p in
             re.findall(_IDENT, m.group(2))]
    if kind == "table":
        if len(parts) > 2:
            sys.exit("COMMENT ON TABLE takes [schema.]table.")
        return (kind, parts[0] if len(parts) == 2 else None, parts[-1], None)
    if len(parts) < 2:
        sys.exit("COMMENT ON COLUMN needs table.column.")
    return (kind, parts[0] if len(parts) == 3 else None, parts[-2], parts[-1])


def sql_string(text):
    """A SQL string literal for text — quotes doubled."""
    return "'" + str(text or "").replace("'", "''") + "'"


def render_comment(kind, table, column, text):
    target = (f"{sql_ident(table)}.{sql_ident(column)}" if kind == "column"
              else sql_ident(table))
    return f"COMMENT ON {kind.upper()} {target} IS {sql_string(text)}\n"


# --- destructive statements --------------------------------------------------

def destructive_reason(sql, group):
    """Why a statement destroys something no journal can bring back, or None.

    Every preview and approval card says this out loud, and an autopilot lease
    never covers it unless the developer asked for destructive statements by
    name. A guess in the safe direction is fine here: a false positive costs
    one question."""
    m = mask_sql(skip_leading_noise(sql)).lower()
    m = " ".join(m.split())
    if group == "structural":
        if m.startswith("create or replace "):
            return ("CREATE OR REPLACE silently overwrites an existing "
                    "definition, and this kind of object has no snapshot")
        if re.match(r"alter sequence\b.*\brestart\b", m):
            return "restarting a sequence can hand out keys already in use"
        if m.startswith(("alter user ", "create user ", "alter role ",
                         "create role ", "alter profile ")):
            return "account and role changes reach every session that uses them"
        if m.startswith("drop "):
            purge = " purge" in m
            return ("DROP removes the object and everything in it"
                    + (" — PURGE skips the Recycle Bin, so not even FLASHBACK "
                       "TABLE can bring it back" if purge else ""))
        if m.startswith("truncate "):
            return "TRUNCATE deletes every row and cannot be rolled back"
        if m.startswith("rename ") or re.search(r"\brename\s+(column|to|constraint|partition)\b", m):
            return "renaming breaks every caller that still uses the old name"
        if re.search(r"\bset\s+unused\b", m):
            return "SET UNUSED makes the column's data unreachable for good"
        if re.search(r"\b(truncate|drop|exchange)\s+(sub)?partition\b", m):
            return "dropping, truncating or exchanging a partition loses rows"
        if m.startswith("alter ") and re.search(r"\bdrop\b", m):
            return "ALTER ... DROP removes a column, constraint or partition"
    if group == "data_dml":
        if re.match(r"(delete|update)\b", m) and not re.search(r"\bwhere\b", m):
            return "no WHERE clause — it touches every row in the table"
        # a predicate that is always true is the same as none
        if re.match(r"(delete|update)\b", m) and re.search(
                r"\bwhere\s+(\(?\s*)?(1\s*=\s*1|0\s*=\s*0|1\s*<>\s*0|true)\b", m):
            return "its WHERE clause is always true — it touches every row"
        if m.startswith("truncate"):
            return "TRUNCATE deletes every row and cannot be rolled back"
    if group == "grants":
        if m.startswith("revoke "):
            return "REVOKE can break other users' and programs' access"
        # security-relevant grants are not a style question: no lint setting
        # (which an agent may edit) can let a lease land them
        if re.search(r"\bto\s+public\b", m):
            return "GRANT ... TO PUBLIC gives every account in the database access"
        head = m.split(" on ")[0] if " on " in m else m
        if re.search(r"\bany\b", head) or re.search(r"\bwith\s+(grant|admin)\s+option\b", m):
            return ("ANY privileges or a grant option reach beyond this "
                    "schema or let the grantee pass the privilege on")
        if re.match(r"grant\s+(dba|resource|connect|sysdba|sysoper)\b", m):
            return "granting a powerful role widens an account for good"
    return None


# --- lint: the house's standards, checked before anything is applied ---------
#
# A finding is (rule, severity, line, message). Severities: error (would be
# wrong in production — autopilot will not land it), warning (a reviewer
# would stop you), info (worth knowing). The LEAD tunes every rule from
# .pythia/conventions.json {"lint": {...}}: severities, required columns,
# mandatory comments, parameter prefixes, forbidden patterns.

LINT_SEVERITIES = ("off", "info", "warning", "error")

LINT_RULES = {
    # rule id: (default severity, why — one line, shown with the finding)
    "when-others-null": ("error", "WHEN OTHERS THEN NULL swallows every error, including TOO_MANY_ROWS: handle what you can name, log and RAISE the rest"),
    "when-others-no-raise": ("warning", "a WHEN OTHERS handler that never re-raises turns every future bug into silent wrong data"),
    "dynamic-sql-concat": ("error", "dynamic SQL built with || is an injection risk and a hard parse per value: use bind variables (USING)"),
    "ddl-in-plsql": ("warning", "DDL through EXECUTE IMMEDIATE commits the caller's transaction implicitly"),
    "commit-in-loop": ("warning", "COMMIT inside a loop leaves a half-applied state on failure and raises ORA-01555 risk"),
    "row-by-row-dml": ("warning", "DML inside a cursor FOR loop switches context per row: use one set-based statement or BULK COLLECT + FORALL"),
    "select-star": ("warning", "SELECT * in code breaks when a column is added: name the columns"),
    "commit-in-trigger": ("error", "COMMIT/ROLLBACK in a trigger raises ORA-04092 (unless autonomous, which is almost always wrong)"),
    "autonomous-transaction": ("info", "PRAGMA AUTONOMOUS_TRANSACTION is for logging and audit only; anywhere else it hides data from the caller's transaction"),
    "raise-app-error-range": ("error", "RAISE_APPLICATION_ERROR codes must be in -20999..-20000"),
    "to-date-no-format": ("warning", "TO_DATE/TO_TIMESTAMP without a format mask depends on the session's NLS settings"),
    "empty-string-compare": ("error", "in Oracle '' IS NULL, so = '' / <> '' is never true: use IS NULL / IS NOT NULL"),
    "rownum-order-by": ("warning", "ROWNUM is applied before ORDER BY: wrap the ordered query, or use FETCH FIRST n ROWS ONLY"),
    "dbms-output": ("info", "DBMS_OUTPUT is debug output; production code logs through the house logger"),
    "goto": ("info", "GOTO makes control flow unreadable; restructure with loops and exits"),
    "varchar-type": ("warning", "VARCHAR may change semantics in future Oracle releases: use VARCHAR2"),
    "long-type": ("error", "LONG / LONG RAW are deprecated and crippled (one per table, no functions): use CLOB / BLOB"),
    "char-type": ("info", "CHAR pads with blanks, so comparisons with VARCHAR2 surprise: prefer VARCHAR2"),
    "varchar2-byte-semantics": ("info", "VARCHAR2(n) defaults to BYTE: in AL32UTF8 a Vietnamese character takes up to 3 bytes — declare VARCHAR2(n CHAR) unless the house rule says otherwise"),
    "hardcoded-size": ("info", "a hand-copied VARCHAR2(n)/NUMBER(p) drifts from the column: anchor with %TYPE"),
    "inout-lob-nocopy": ("info", "IN OUT CLOB/BLOB copies on entry and exit: add NOCOPY"),
    "non-ascii-literal": ("warning", "raw non-ASCII in a literal is corrupted by client/DB charset mismatches: use `pythia unistr`"),
    "substitution-variable": ("warning", "&name is a SQL*Plus substitution; pythia sends it to the database literally"),
    "sqlplus-directive": ("info", "SQL*Plus directives run nothing on the database: deploy skips them, apply refuses a file that holds one"),
    "table-no-pk": ("warning", "a table without a primary key cannot be referenced, replicated or deduplicated safely"),
    "constraint-unnamed": ("info", "unnamed constraints get SYS_C names that differ per environment: name them"),
    "add-not-null-no-default": ("warning", "ADD ... NOT NULL without DEFAULT fails on a populated table (ORA-01758)"),
    "fk-no-index": ("info", "a foreign key without an index on its columns locks the child table on parent DELETE/UPDATE"),
    "comment-missing": ("warning", "every new table and column needs COMMENT ON — the data dictionary is the documentation"),
    "required-column-missing": ("warning", "the house requires these columns on every table"),
    "drop-purge": ("warning", "DROP ... PURGE skips the Recycle Bin: there is no FLASHBACK TABLE ... TO BEFORE DROP afterwards"),
    "dml-no-where": ("error", "UPDATE/DELETE without WHERE touches every row"),
    "merge-refused": ("error", "apply refuses MERGE: its row set cannot be revalidated — split into UPDATE and INSERT"),
    "insert-no-columns": ("warning", "INSERT without a column list breaks when a column is added"),
    "grant-to-public": ("error", "GRANT ... TO PUBLIC gives every account in the database access"),
    "grant-any": ("error", "ANY privileges reach every schema in the database"),
    "grant-option": ("warning", "WITH GRANT/ADMIN OPTION lets the grantee pass the privilege on"),
    "param-prefix": ("warning", "parameter names follow the house prefix"),
    "naming": ("warning", "object names follow the house pattern in conventions.json"),
}

LINT_CONFIG_KEYS = {"rules", "require_comments", "required_columns",
                    "param_prefix", "forbid"}


def lint_config(conv):
    """The LEAD's lint block from conventions.json, validated. Config is a
    trust boundary: unknown keys and values are refused with the fix."""
    cfg = (conv or {}).get("lint") or {}
    if not isinstance(cfg, dict):
        sys.exit('conventions.json "lint" must be an object.')
    unknown = set(cfg) - LINT_CONFIG_KEYS
    if unknown:
        sys.exit(f"Unknown lint key {sorted(unknown)[0]!r} in conventions.json "
                 f"— supported: {', '.join(sorted(LINT_CONFIG_KEYS))}.")
    for rule, sev in (cfg.get("rules") or {}).items():
        if rule not in LINT_RULES:
            sys.exit(f"Unknown lint rule {rule!r} in conventions.json. "
                     f"Rules: {', '.join(sorted(LINT_RULES))}.")
        if sev not in LINT_SEVERITIES:
            sys.exit(f"Lint rule {rule!r} must be one of "
                     f"{', '.join(LINT_SEVERITIES)} — got {sev!r}.")
    for f in cfg.get("forbid") or []:
        if not isinstance(f, dict) or not f.get("pattern") or not f.get("id"):
            sys.exit('Each conventions.json "lint.forbid" entry needs "id" '
                     'and "pattern" (and optionally "message", "severity", '
                     '"groups").')
        try:
            re.compile(f["pattern"])
        except re.error as e:
            sys.exit(f"Bad lint.forbid regex {f['id']!r}: {e}")
        if f.get("severity", "warning") not in LINT_SEVERITIES:
            sys.exit(f"lint.forbid {f['id']!r}: bad severity.")
    if cfg.get("param_prefix"):
        try:
            re.compile(cfg["param_prefix"])
        except re.error as e:
            sys.exit(f"Bad lint.param_prefix regex: {e}")
    rc = cfg.get("required_columns")
    if rc is not None and not (isinstance(rc, list)
                               and all(isinstance(c, str) for c in rc)):
        sys.exit('lint.required_columns must be a list of column names.')
    return cfg


def _line_of(text, idx):
    return text.count("\n", 0, idx) + 1


def _top_level_split(body):
    """Split masked text on commas at parenthesis depth 0 — the column and
    constraint clauses of a table body. Returns (start, segment) pairs."""
    out, depth, start = [], 0, 0
    for i, c in enumerate(body):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            out.append((start, body[start:i]))
            start = i + 1
    out.append((start, body[start:]))
    return out


def _paren_body(masked, open_idx):
    """(start, end) of the text inside the parenthesis opening at open_idx."""
    depth = 0
    for i in range(open_idx, len(masked)):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return open_idx + 1, i
    return open_idx + 1, len(masked)


def lint_statement(text, group, conv=None):
    """Findings for one statement: [(rule, line, message)], lines 1-based
    within the statement. Severities are applied by lint_findings."""
    cfg = lint_config(conv)
    masked = mask_sql(text)
    up = masked.upper()
    found = []

    def hit(rule, idx, detail=""):
        found.append((rule, _line_of(text, idx),
                      detail or LINT_RULES[rule][1]))

    plsql = group in ("plsql_source", "anonymous")
    if plsql:
        for m in re.finditer(r"\bWHEN\s+OTHERS\s+THEN\s+NULL\s*;", up):
            hit("when-others-null", m.start())
        for m in re.finditer(r"\bWHEN\s+OTHERS\s+THEN\b", up):
            tail = up[m.end():]
            stop = re.search(r"\bWHEN\b|\bEND\b", tail)
            handler = tail[:stop.start()] if stop else tail
            if re.match(r"\s*NULL\s*;", handler):
                continue                       # reported as when-others-null
            if not re.search(r"\bRAISE(_APPLICATION_ERROR)?\b", handler):
                hit("when-others-no-raise", m.start())
        # concatenation through DBMS_ASSERT is the sanctioned way to put an
        # identifier into dynamic SQL — not the antipattern
        for m in re.finditer(r"\bEXECUTE\s+IMMEDIATE\b([^;]*)", up):
            if "||" in m.group(1) and "DBMS_ASSERT" not in m.group(1):
                hit("dynamic-sql-concat", m.start())
        for m in re.finditer(r"\bOPEN\s+\w+\s+FOR\b([^;]*)", up):
            if "||" in m.group(1) and "DBMS_ASSERT" not in m.group(1):
                hit("dynamic-sql-concat", m.start())
        for m in re.finditer(r"EXECUTE\s+IMMEDIATE\s+'\s*(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b",
                             text, re.I):
            hit("ddl-in-plsql", m.start())
        # loop-aware scan: COMMIT in any loop; DML in a cursor FOR loop
        stack, pos = [], 0
        for line in up.split("\n"):
            if re.search(r"\bEND\s+LOOP\b", line):
                if stack:
                    stack.pop()
            elif re.search(r"\bLOOP\b", line):
                stack.append(bool(re.search(r"\bFOR\s+\w+\s+IN\s*\(|\bFOR\s+\w+\s+IN\s+[A-Z_]\w*\s*(\(|LOOP)", line)))
            elif stack:
                if re.search(r"^\s*COMMIT\b", line):
                    hit("commit-in-loop", pos)
                if any(stack) and re.search(r"^\s*(INSERT|UPDATE|DELETE|MERGE)\b", line):
                    hit("row-by-row-dml", pos)
            pos += len(line) + 1
        for m in re.finditer(r"\bSELECT\s+\*\s+(INTO|FROM|BULK)\b", up):
            hit("select-star", m.start())
        if (created_object(text) or ("",))[0] == "TRIGGER" \
                and "AUTONOMOUS_TRANSACTION" not in up:
            for m in re.finditer(r"\b(COMMIT|ROLLBACK)\s*;", up):
                hit("commit-in-trigger", m.start())
        for m in re.finditer(r"\bPRAGMA\s+AUTONOMOUS_TRANSACTION\b", up):
            hit("autonomous-transaction", m.start())
        for m in re.finditer(r"\bRAISE_APPLICATION_ERROR\s*\(\s*(-?\s*\d+)", up):
            code = int(m.group(1).replace(" ", ""))
            if not -20999 <= code <= -20000:
                hit("raise-app-error-range", m.start(),
                    f"RAISE_APPLICATION_ERROR({code}) — codes must be in "
                    "-20999..-20000")
        for m in re.finditer(r"\bDBMS_OUTPUT\s*\.\s*PUT", up):
            hit("dbms-output", m.start())
        for m in re.finditer(r"\bGOTO\s+\w", up):
            hit("goto", m.start())
        decl = re.search(r"\b(IS|AS)\b(.*?)\bBEGIN\b", up, re.S)
        if decl:
            for m in re.finditer(r"^\s*\w+\s+(CONSTANT\s+)?(VARCHAR2|NUMBER)\s*\(\s*\d",
                                 decl.group(2), re.M):
                hit("hardcoded-size", decl.start(2) + m.start())
        for m in re.finditer(r"\bIN\s+OUT\s+(?!NOCOPY\b)(CLOB|BLOB|NCLOB)\b", up):
            hit("inout-lob-nocopy", m.start())
        if cfg.get("param_prefix"):
            rx = re.compile(cfg["param_prefix"], re.I)
            for m in re.finditer(r"\b(PROCEDURE|FUNCTION)\s+[\w$#\".]+\s*\(", up):
                b0, b1 = _paren_body(masked, m.end() - 1)
                for off, seg in _top_level_split(masked[b0:b1]):
                    name = (seg.split() or [""])[0].strip('"')
                    if name and not rx.match(name):
                        hit("param-prefix", b0 + off,
                            f"parameter {name} does not match the house "
                            f"prefix {cfg['param_prefix']}")
    # rules that apply wherever SQL appears
    for m in re.finditer(r"\b(TO_DATE|TO_TIMESTAMP)\s*\(([^(),]*)\)", up):
        hit("to-date-no-format", m.start())
    for a, b in literal_spans(text):
        if text[a:b] == "''":
            before = masked[:a].rstrip()
            if before.endswith(("=", "<>", "!=")) and not before.endswith(":="):
                hit("empty-string-compare", a)
        # COMMENT ON takes a literal only — unistr() is not allowed there, and
        # pythia's thin driver sends UTF-8 exactly, so there is nothing to fix
        if group != "comments" and any(ord(ch) > 127 for ch in text[a:b]):
            hit("non-ascii-literal", a)
    for m in re.finditer(r"\bROWNUM\s*(<=?|=)\s*[\w:]+\s+ORDER\s+BY\b", up):
        hit("rownum-order-by", m.start())
    for m in re.finditer(r"(?<![\w&])&{1,2}[A-Za-z_]\w*", masked):
        hit("substitution-variable", m.start())
    for m in re.finditer(r"\bVARCHAR\s*\(", up):
        hit("varchar-type", m.start())
    for m in re.finditer(r"\bLONG(\s+RAW)?\s*(,|\)|NOT\b|NULL\b|DEFAULT\b|;|$)", up, re.M):
        hit("long-type", m.start())
    for m in re.finditer(r"(?<![\w$#])CHAR\s*\(\s*\d", up):
        hit("char-type", m.start())

    s = " ".join(up.split())
    if group == "structural":
        ct = re.search(r"\bCREATE\s+(GLOBAL\s+TEMPORARY\s+|PRIVATE\s+TEMPORARY\s+)?TABLE\b", up)
        if ct and re.match(r"\s*CREATE\b", up):
            p = up.find("(", ct.end())
            as_select = re.match(r"CREATE\s+(\w+\s+)*TABLE\s+[\w$#\".]+\s+AS\s+(SELECT|WITH)\b", s)
            if p >= 0 and not as_select:
                b0, b1 = _paren_body(masked, p)
                segs = _top_level_split(masked[b0:b1])
                if "PRIMARY KEY" not in " ".join(up[b0:b1].split()):
                    hit("table-no-pk", ct.start())
                for off, seg in segs:
                    if re.search(r"\b(PRIMARY\s+KEY|UNIQUE|REFERENCES|CHECK\s*\()", seg.upper()) \
                            and not re.search(r"\bCONSTRAINT\b", seg.upper()):
                        hit("constraint-unnamed", b0 + off)
                    if re.search(r"\bVARCHAR2\s*\(\s*\d+\s*\)", seg.upper()):
                        hit("varchar2-byte-semantics", b0 + off)
                cols = [((seg.split() or [""])[0]).strip('"').upper()
                        for _, seg in segs
                        if seg.split() and not re.match(
                            r"\s*(CONSTRAINT|PRIMARY|UNIQUE|FOREIGN|CHECK|SUPPLEMENTAL)\b",
                            seg.upper())]
                need = [c.upper() for c in (cfg.get("required_columns") or [])]
                missing = [c for c in need if c not in cols]
                if missing:
                    hit("required-column-missing", ct.start(),
                        f"missing house column(s): {', '.join(missing)}")
        if re.match(r"ALTER\s+TABLE\b", s):
            add = re.search(r"\bADD\s*\(", up)
            segs = []
            if add:
                b0, b1 = _paren_body(masked, add.end() - 1)
                segs = [(b0 + off, seg) for off, seg in _top_level_split(masked[b0:b1])]
            else:
                add = re.search(r"\bADD\s+(?!CONSTRAINT\b|PRIMARY\b|UNIQUE\b|FOREIGN\b|CHECK\b|PARTITION\b|SUPPLEMENTAL\b)", up)
                if add:
                    segs = [(add.end(), masked[add.end():])]
            for off, seg in segs:
                u = " ".join(seg.upper().split())
                if re.search(r"\bNOT\s+NULL\b", u) and "DEFAULT" not in u:
                    hit("add-not-null-no-default", off)
                if re.search(r"\bVARCHAR2\s*\(\s*\d+\s*\)", u):
                    hit("varchar2-byte-semantics", off)
        if re.match(r"DROP\s+TABLE\b.*\bPURGE\b", s):
            hit("drop-purge", 0)
    if group == "data_dml":
        if re.match(r"(UPDATE|DELETE)\b", s) and not re.search(r"\bWHERE\b", s):
            hit("dml-no-where", 0)
        if s.startswith("MERGE"):
            hit("merge-refused", 0)
        if re.match(r"INSERT\s+INTO\s+[\w$#\".]+(\s+\w+)?\s+(VALUES|SELECT|WITH)\b", s):
            hit("insert-no-columns", 0)
    if group == "grants":
        if re.search(r"\bTO\s+PUBLIC\b", s):
            hit("grant-to-public", 0)
        if re.search(r"\b\w+\s+ANY\s+\w+", s.split(" ON ")[0] if " ON " in s else s) \
                and s.startswith("GRANT"):
            hit("grant-any", 0)
        if re.search(r"\bWITH\s+(GRANT|ADMIN)\s+OPTION\b", s):
            hit("grant-option", 0)
    # the LEAD's naming patterns, for every object kind a statement creates
    created = created_object(text, group)
    if created:
        msg = naming_violation(created[0], created[1], conv)
        if msg:
            hit("naming", 0, msg)
    for f in cfg.get("forbid") or []:
        groups = f.get("groups")
        if groups and group not in groups:
            continue
        for m in re.finditer(f["pattern"], masked, re.I):
            found.append((f"forbid:{f['id']}", _line_of(text, m.start()),
                          f.get("message") or f"forbidden by the house: {f['pattern']}"))
    return found


CREATED_KIND_RE = re.compile(
    r"^create\s+(?:or\s+replace\s+)?(?:(?:non?)?editionable\s+|(?:no)?force\s+|"
    r"global\s+temporary\s+|private\s+temporary\s+|unique\s+|bitmap\s+)*"
    r"(table|index|sequence|view|synonym|materialized\s+view|procedure|"
    r"function|package\s+body|package|trigger|type\s+body|type)\s+"
    r"(" + _IDENT + r"(?:\s*\.\s*" + _IDENT + r")?)", re.I | re.S)


def created_object(text, group=None):
    """(OBJECT TYPE, NAME) a CREATE statement brings into being, or None."""
    m = CREATED_KIND_RE.match(skip_leading_noise(text))
    if not m:
        return None
    kind = " ".join(m.group(1).upper().split())
    name = _ident_value(re.findall(_IDENT, m.group(2))[-1])
    return kind, name


def lint_severity(rule, cfg):
    if rule.startswith("forbid:"):
        rid = rule.split(":", 1)[1]
        for f in cfg.get("forbid") or []:
            if f["id"] == rid:
                return f.get("severity", "warning")
        return "warning"
    if rule == "comment-missing" and "require_comments" in cfg:
        if cfg["require_comments"] is False:
            return "off"
        if cfg["require_comments"] in LINT_SEVERITIES:
            return cfg["require_comments"]
    return (cfg.get("rules") or {}).get(rule, LINT_RULES[rule][0])


def lint_findings(text, group, conv=None):
    """Statement-level findings as dicts with their effective severity —
    what the apply preview shows and the autopilot gate reads."""
    cfg = lint_config(conv)
    out = []
    for rule, ln, msg in lint_statement(text, group, conv):
        sev = lint_severity(rule, cfg)
        if sev != "off":
            out.append({"rule": rule, "severity": sev, "line": ln,
                        "message": msg})
    order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda f: (order[f["severity"]], f["line"], f["rule"]))
    return out


def script_comment_targets(text):
    """(TABLE, COLUMN) pairs a script comments on — COLUMN is "" for the
    table itself. What a whole release set contributes to comment-missing."""
    out = set()
    try:
        stmts, _ = split_script(text)
    except SystemExit:
        return out
    for st in stmts:
        if classify(st["text"]) == "comments":
            try:
                _, _, t, c = parse_comment_target(st["text"])
                out.add((t.upper(), (c or "").upper()))
            except SystemExit:
                pass
    return out


def lint_script(text, conv=None, set_comments=None):
    """Lint a whole file: every statement, plus the rules only a whole script
    can answer (a new table's columns all commented, FKs indexed). Returns
    findings as dicts sorted by line: rule, severity, line, message.
    `set_comments`: the COMMENT ON targets of every file linted with this one
    — a release keeps its comments in their own file."""
    cfg = lint_config(conv)
    stmts, skipped = split_script(text)
    out = []
    for sk in skipped:
        out.append(("sqlplus-directive", sk["line"],
                    f"{sk['text']!r}: " + LINT_RULES["sqlplus-directive"][1]))
    tables, comments, indexed, fks = {}, set(), [], []
    for st in stmts:
        group = classify(st["text"]) or "unknown"
        for rule, ln, msg in lint_statement(st["text"], group, conv):
            out.append((rule, st["line"] + ln - 1, msg))
        masked = " ".join(mask_sql(st["text"]).upper().split())
        made = created_object(st["text"])
        if made and made[0] == "TABLE" and "(" in masked and \
                not re.search(r"\bAS\s+(SELECT|WITH)\b", masked.split("(")[0]):
            body = mask_sql(st["text"])
            p = body.find("(")
            b0, b1 = _paren_body(body, p)
            cols = [((seg.split() or [""])[0]).strip('"').upper()
                    for _, seg in _top_level_split(body[b0:b1])
                    if seg.split() and not re.match(
                        r"\s*(CONSTRAINT|PRIMARY|UNIQUE|FOREIGN|CHECK|SUPPLEMENTAL)\b",
                        seg.upper())]
            tables[made[1]] = (st["line"], cols)
            for fm in re.finditer(r"FOREIGN\s+KEY\s*\(([^)]*)\)", masked):
                fks.append((made[1], [c.strip().strip('"') for c in fm.group(1).split(",")],
                            st["line"]))
        am = re.match(r"ALTER\s+TABLE\s+([\w$#\".]+)\s+ADD\b", masked)
        if am:
            tname = am.group(1).split(".")[-1].strip('"')
            for fm in re.finditer(r"FOREIGN\s+KEY\s*\(([^)]*)\)", masked):
                fks.append((tname, [c.strip().strip('"') for c in fm.group(1).split(",")],
                            st["line"]))
        if group == "comments":
            try:
                kind, _, t, c = parse_comment_target(st["text"])
                comments.add((t.upper(), (c or "").upper()))
            except SystemExit:
                pass
        im = re.match(r"CREATE\s+(UNIQUE\s+|BITMAP\s+)?INDEX\s+[\w$#\".]+\s+ON\s+([\w$#\".]+)\s*\(([^)]*)\)",
                      masked)
        if im:
            indexed.append((im.group(2).split(".")[-1].strip('"'),
                            [c.strip().strip('"').split()[0] for c in im.group(3).split(",") if c.strip()]))
        for pm in re.finditer(r"(PRIMARY\s+KEY|UNIQUE)\s*\(([^)]*)\)", masked):
            if made and made[0] == "TABLE":
                indexed.append((made[1], [c.strip().strip('"') for c in pm.group(2).split(",")]))
    where = "in this script" if set_comments is None else "in the files linted"
    comments |= set_comments or set()
    for t, (ln, cols) in tables.items():
        missing = [c for c in cols if (t, c) not in comments]
        if (t, "") not in comments:
            out.append(("comment-missing", ln, f"table {t} has no COMMENT ON TABLE {where}"))
        if missing:
            out.append(("comment-missing", ln,
                        f"columns of {t} without COMMENT ON COLUMN: {', '.join(missing)}"))
    for t, cols, ln in fks:
        ok = any(it == t and [c.upper() for c in ic[:len(cols)]] == [c.upper() for c in cols]
                 for it, ic in indexed)
        if not ok:
            out.append(("fk-no-index", ln,
                        f"FK on {t}({', '.join(cols)}) has no index in this script "
                        "— check `pythia stats " + t + "` if it exists already"))
    findings = []
    for rule, ln, msg in out:
        sev = lint_severity(rule, cfg)
        if sev == "off":
            continue
        findings.append({"rule": rule, "severity": sev, "line": ln,
                         "message": msg})
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (f["line"], order[f["severity"]], f["rule"]))
    return findings


def lint_summary(findings):
    n = {s: sum(1 for f in findings if f["severity"] == s)
         for s in ("error", "warning", "info")}
    return f"{n['error']} error(s), {n['warning']} warning(s), {n['info']} info"


def render_findings(findings, label="", en=False, limit=None):
    color = {"error": "red", "warning": "yellow", "info": "dim"}
    lines = []
    for f in findings[:limit] if limit else findings:
        where = f"{label}:{f['line']}" if label else f"line {f['line']}"
        lines.append(paint(f"{where}: {f['severity']} [{f['rule']}] "
                           f"{f['message']}", color[f["severity"]], en))
    if limit and len(findings) > limit:
        lines.append(f"-- truncated: {len(findings) - limit} more finding(s); "
                     f"{invocation()} lint <file> shows all")
    return lines


# --- autopilot: the developer delegates a session, once ----------------------
#
# Approving every write is right for a careful change and wrong for a day of
# building a module: twenty approvals teach the developer to click without
# reading, which is worse than one honest delegation. A lease is that one act:
# the developer approves a card naming the connection, the statement groups,
# the duration and a write budget — through the same doors as any approval —
# and until it ends, apply lands covered statements without asking.
#
# What a lease never changes: the snapshot, the journal, the verify, the exit
# codes. What it never covers unless the card said so by name: destructive
# statements. What ends it early: breaking objects that were valid (pause),
# five failures in a row (pause), the budget, the clock, `autopilot off`.

LEASE_GROUPS_DEFAULT = ("plsql_source", "comments")
LEASE_GROUPS_ALL = ("plsql_source", "comments", "structural", "data_dml",
                    "grants")
LEASE_HOURS_DEFAULT = 8
LEASE_HOURS_MAX = 24
LEASE_WRITES_DEFAULT = 200
LEASE_WRITES_MAX = 2000
LEASE_FAILS_TO_PAUSE = 5
LEASE_DML_MAX_ROWS = 1000      # a bigger row set is shown to a person first
PROTECTED_ENVS = {"prod", "production", "prd", "live"}

GROUP_UNDO = {
    "plsql_source": "restorable from the journal",
    "comments": "restorable from the journal",
    "structural": "NO real undo (DROP COLUMN is permanent)",
    "data_dml": "NO undo after commit (row set revalidated first)",
    "grants": "reversible by hand",
    "session": "nothing persists",
}


def autopilot_root(root):
    return pathlib.Path(root) / CONFIG_DIR / "autopilot"


def lease_file(root):
    return autopilot_root(root) / "lease.json"


def lease_request_file(root, token):
    return autopilot_root(root) / "requests" / f"{token}.json"


def _read_json(path):
    """A JSON object from disk, or None. Half-written or corrupt state is
    absence, never permission."""
    try:
        rec = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return rec if isinstance(rec, dict) else None


def _write_json(path, rec):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=2, default=str) + "\n",
                    encoding="utf-8")


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _parse_iso(text):
    import datetime
    try:
        return datetime.datetime.fromisoformat(str(text))
    except ValueError:
        return None


def connection_protected(entry):
    """A connection the developer marked as never-automate: "protected": true,
    or "env": "prod" / "production" / "live" in connections.json."""
    e = entry or {}
    return bool(e.get("protected")) or \
        str(e.get("env", "")).strip().lower() in PROTECTED_ENVS


def parse_lease_groups(text):
    if not text:
        return list(LEASE_GROUPS_DEFAULT)
    groups = []
    for g in str(text).replace(" ", "").split(","):
        if not g:
            continue
        if g == "all":
            groups.extend(LEASE_GROUPS_ALL)
            continue
        if g not in LEASE_GROUPS_ALL:
            sys.exit(f"Autopilot cannot cover {g!r}. Groups: "
                     f"{', '.join(LEASE_GROUPS_ALL)} (or all).")
        groups.append(g)
    return sorted(set(groups), key=LEASE_GROUPS_ALL.index)


def new_lease_request(root, conn_name, conn_entry, groups=None, hours=None,
                      max_writes=None, destructive=False, note="", now=None):
    """Write a pending lease request and return it. Nothing is delegated yet:
    the request is a card waiting for the developer's Approve."""
    import datetime
    import secrets
    if conn_name == "env":
        sys.exit("Refused: autopilot needs a named connection from "
                 f"{CONFIG_NAME}, where it can be marked protected — "
                 "PYTHIA_USER/PYTHIA_PASSWORD/PYTHIA_DSN credentials cannot be.")
    if connection_protected(conn_entry):
        sys.exit(f"Refused: connection {conn_name!r} is marked protected in "
                 f"{CONFIG_NAME}. Autopilot never covers it — every write "
                 "there is approved one at a time.")
    hours = LEASE_HOURS_DEFAULT if hours is None else hours
    if not 0 < hours <= LEASE_HOURS_MAX:
        sys.exit(f"--hours must be between 0 and {LEASE_HOURS_MAX}.")
    max_writes = LEASE_WRITES_DEFAULT if max_writes is None else max_writes
    if not 0 < max_writes <= LEASE_WRITES_MAX:
        sys.exit(f"--max-writes must be between 1 and {LEASE_WRITES_MAX}.")
    now = now or datetime.datetime.now()
    req = {"kind": "autopilot", "token": secrets.token_hex(4),
           "conn": conn_name or "", "groups": list(groups or LEASE_GROUPS_DEFAULT),
           "hours": hours, "max_writes": int(max_writes),
           "destructive": bool(destructive), "note": note or "",
           "requested_at": _iso(now),
           "request_expires_at": _iso(now + datetime.timedelta(
               minutes=GRANT_TTL_MINUTES))}
    _write_json(lease_request_file(root, req["token"]), req)
    return req


def lease_card(req, root):
    """The card a developer approves a lease on — every consequence named."""
    eff = load_policy(root)
    hours = req["hours"]
    span = (f"{hours:g} hour{'s' if hours != 1 else ''}")
    lines = [f"Autopilot on connection {req['conn'] or '?'}: the agent applies "
             f"without asking, for {span} or {req['max_writes']} writes, "
             "whichever ends first."]
    for g in req["groups"]:
        extra = (" — policy says deny; this lease overrides it while active"
                 if eff.get(g, ("confirm",))[0] == "deny" else "")
        lines.append(f"  {g:<13} {GROUP_UNDO[g]}{extra}")
    if req.get("destructive"):
        lines.append("  DESTRUCTIVE statements INCLUDED (DROP, TRUNCATE, "
                     "ALTER ... DROP, REVOKE, UPDATE/DELETE without WHERE) "
                     "— none of them can be undone.")
    else:
        lines.append("  Destructive statements (DROP, TRUNCATE, ALTER ... "
                     "DROP, REVOKE, UPDATE/DELETE without WHERE) still need "
                     "your approval each time.")
    lines.append("Every write still snapshots, journals and verifies; lint "
                 "errors are not landed; breaking other objects pauses it.")
    if req.get("note"):
        lines.append(f"Task, as the agent described it: {req['note']}")
    # no invocation() here: the agent prints this card with one spelling and
    # the hook re-renders it with another, and the two must match verbatim
    lines.append(f"Requested {str(req['requested_at']).replace('T', ' ')}. "
                 "Revoke any time: pythia autopilot off")
    return lines


def read_lease(root):
    return _read_json(lease_file(root))


def lease_status(lease, now=None):
    """active, paused, expired, spent, revoked — or None for no lease."""
    import datetime
    if not lease:
        return None
    if lease.get("revoked_at"):
        return "revoked"
    now = now or datetime.datetime.now()
    exp = _parse_iso(lease.get("expires_at"))
    if exp is None or now > exp:
        return "expired"
    if int(lease.get("writes", 0)) >= int(lease.get("max_writes", 0)):
        return "spent"
    if lease.get("paused"):
        return "paused"
    return "active"


def lease_line(lease, now=None):
    """One line saying what the lease is doing right now."""
    st = lease_status(lease, now)
    if st is None:
        return "autopilot: off"
    base = (f"autopilot: {st.upper()} on {lease.get('conn')} — "
            f"{', '.join(lease.get('groups', []))}"
            f"{' + destructive' if lease.get('destructive') else ''}; "
            f"{lease.get('writes', 0)}/{lease.get('max_writes')} writes; "
            f"until {str(lease.get('expires_at', '?'))[11:16]}")
    if st == "paused":
        base += f" — paused: {lease['paused'].get('reason')}"
    return base


def connection_entry(root, conn_name):
    """The connections.json entry for a name, or None — read fresh, so a
    connection marked protected after a lease began stops being covered."""
    cfg = _read_json(pathlib.Path(root) / CONFIG_DIR / CONFIG_NAME) or {}
    for k, v in cfg.items():
        if k.upper() == str(conn_name or "").upper() and isinstance(v, dict):
            return v
    return None


def lease_names_group(root, conn_name, group, now=None):
    """Whether an active lease on this connection names the group. That is
    the developer's override of a `deny` policy for the group — what the
    card says next to it — and it holds for previews and one-at-a-time
    approvals too, not only for what the lease lands by itself."""
    lease = read_lease(root)
    return (lease_status(lease, now) == "active"
            and str(lease.get("conn", "")).upper() == str(conn_name or "").upper()
            and not connection_protected(connection_entry(root, conn_name))
            and group in lease.get("groups", []))


def autopilot_check(root, conn_name, group, text, now=None):
    """(covered, lease, why): whether the active lease lands this statement
    without a per-write approval, and if not, why — said in the preview, so
    the agent knows what to ask for instead of guessing."""
    lease = read_lease(root)
    st = lease_status(lease, now)
    if st is None:
        return False, None, None
    if st != "active":
        why = f"autopilot is {st}"
        if st == "paused":
            why += f" ({lease['paused'].get('reason')})"
        return False, lease, why
    if str(lease.get("conn", "")).upper() != str(conn_name or "").upper():
        return False, lease, (f"autopilot covers connection {lease.get('conn')}, "
                              f"not {conn_name}")
    if connection_protected(connection_entry(root, conn_name)):
        return False, lease, (f"connection {conn_name} is marked protected — "
                              "autopilot never covers it")
    if group not in lease.get("groups", []):
        return False, lease, f"autopilot does not cover {group} statements"
    d = destructive_reason(text, group)
    if d and not lease.get("destructive"):
        return False, lease, (f"destructive — {d}; the lease leaves that to "
                              "the developer, one approval at a time")
    return True, lease, None


def activate_lease(root, req, approver, session=None, now=None):
    """The developer approved the card: the lease starts now. The previous
    lease, if any, moves to history — its report stays readable."""
    import datetime
    now = now or datetime.datetime.now()
    old = read_lease(root)
    if old:
        _write_json(autopilot_root(root) / "history" / f"{old.get('token')}.json",
                    old)
    lease = {k: v for k, v in req.items() if k != "request_expires_at"}
    lease.update(kind="lease", approved_at=_iso(now),
                 expires_at=_iso(now + datetime.timedelta(hours=req["hours"])),
                 approver=approver, session=session or None, writes=0,
                 fails_in_a_row=0, paused=None, revoked_at=None)
    _write_json(lease_file(root), lease)
    try:
        lease_request_file(root, req["token"]).unlink()
    except OSError:
        pass
    return lease


def lease_note_write(root, ok, broke, entry, written=True, now=None):
    """Count one autopilot write, and pause the lease when the evidence says a
    person should look: other objects broke, or failures keep repeating."""
    import datetime
    lease = read_lease(root)
    if not lease:
        return None
    now = now or datetime.datetime.now()
    if written:
        lease["writes"] = int(lease.get("writes", 0)) + 1
    lease["fails_in_a_row"] = 0 if ok else int(lease.get("fails_in_a_row", 0)) + 1
    if broke:
        lease["paused"] = {"reason": f"{len(broke)} object(s) that were VALID "
                                     "are INVALID now", "entry": entry,
                           "at": _iso(now)}
    elif lease["fails_in_a_row"] >= LEASE_FAILS_TO_PAUSE:
        lease["paused"] = {"reason": f"{lease['fails_in_a_row']} failed writes "
                                     "in a row", "entry": entry, "at": _iso(now)}
    _write_json(lease_file(root), lease)
    return lease


def revoke_lease(root, now=None):
    import datetime
    lease = read_lease(root)
    if not lease or lease.get("revoked_at"):
        return None
    lease["revoked_at"] = _iso(now or datetime.datetime.now())
    _write_json(lease_file(root), lease)
    return lease


def lease_writes(root, token):
    """Journal entries written under one lease, oldest first."""
    out = []
    for eid in reversed(list_journal_entries(root)):
        try:
            m = read_journal_entry(root, eid)["meta"]
        except SystemExit:
            continue
        if m.get("autopilot") == token:
            out.append((eid, m))
    return out


# --- deploy batches: one approval for an ordered set of statements -----------

def deploys_root(root):
    return pathlib.Path(root) / CONFIG_DIR / "deploys"


def batch_file(root, token):
    return deploys_root(root) / f"{token}.json"


def write_batch(root, conn_name, schema, files, members, now=None, start=1):
    import datetime
    import hashlib
    now = now or datetime.datetime.now()
    seed = "\n".join([conn_name or "", schema or "", *files,
                      *(m["token"] for m in members), _iso(now)])
    token = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
    batch = {"kind": "batch", "token": token, "conn": conn_name or "",
             "schema": schema, "files": list(files), "members": members,
             "from": int(start or 1), "created_at": _iso(now),
             "applied": False}
    _write_json(batch_file(root, token), batch)
    return batch


def batch_card(batch):
    """The card for a whole release: every statement as it will run — in
    full for every group without a snapshot, with its row set for DML —
    because what is approved has to be what was seen, one statement or
    forty."""
    n = len(batch["members"])
    lines = [f"Approving a deploy of {n} statement{'s' if n != 1 else ''} on "
             f"{batch.get('schema') or '?'}, in this order:"]
    for m in batch["members"]:
        if m["group"] == "plsql_source":
            changed = (" — new object" if m.get("created") else
                       f" — {m['changed']} lines changed"
                       if m.get("changed") is not None else "")
            lines.append(f"  {m['n']:>2}. [{m['group']}] {m.get('object')} "
                         f"({m.get('type')}){changed}")
            if m.get("summary"):
                lines.append(f"      {m['summary'].lstrip('- ')}")
        else:
            lines.append(f"  {m['n']:>2}. [{m['group']}]")
            lines += [f"      {ln}" for ln in
                      str(m.get("statement") or "").strip().splitlines()]
            if m.get("row_set"):
                lines += render_row_set(m["row_set"], False, indent="      ")
        if m.get("destructive"):
            lines.append(f"      ! DESTRUCTIVE: {m['destructive']}.")
        if m.get("lint_errors"):
            lines.append(f"      ! {m['lint_errors']} lint error(s) — see the preview")
    undo = sorted({m["group"] for m in batch["members"]}
                  - {"plsql_source", "comments"})
    if undo:
        lines.append(f"No snapshot can undo {', '.join(undo)} after commit; "
                     "plsql_source and comments are restorable per entry.")
    lines.append("It stops at the first refusal or broken result, and an "
                 "approval left unused by a stop is withdrawn.")
    start = int(batch.get("from") or 1)
    if start > 1:
        lines.append(f"Resumes the files at statement {start}; the statements "
                     "before it are not part of this approval.")
    lines.append(f"Previewed {str(batch['created_at']).replace('T', ' ')} on "
                 f"connection {batch.get('conn') or '?'}.")
    return lines


# --- one approval, any door --------------------------------------------------

def find_approvable(root, token):
    """(kind, entry, meta) for anything a person can approve by its token: a
    preview (6 hex), an autopilot lease request (8), a deploy batch (10)."""
    if not valid_token(token):
        return None, None, None
    entry, meta = find_preview_by_token(root, token)
    if meta:
        return "preview", entry, meta
    req = _read_json(lease_request_file(root, token)) if re.fullmatch(
        r"[0-9a-f]{8}", token or "") else None
    if req:
        return "autopilot", f"autopilot:{token}", req
    batch = _read_json(batch_file(root, token)) if re.fullmatch(
        r"[0-9a-f]{10}", token or "") else None
    if batch:
        return "batch", f"deploy:{token}", batch
    return None, None, None


def grant_approval(root, token, meta, approver, session=None):
    """Record a developer's Approve for whatever the token names, and return
    the sentence that tells the agent what it may do now. The console, the
    chat hook and the MCP elicitation all end here, so no door can grant
    more than another."""
    kind = (meta or {}).get("kind", "preview")
    prune_expired_grants(root)
    if kind == "autopilot":
        lease = activate_lease(root, meta, approver, session)
        return (f"autopilot ACTIVE on {lease['conn']} until "
                f"{lease['expires_at'][11:16]} — {', '.join(lease['groups'])}, "
                f"up to {lease['max_writes']} writes. Apply directly now: "
                f"{invocation()} apply <file> or {invocation()} deploy <files> "
                "(no --confirm needed while it lasts).")
    if kind == "batch":
        for m in meta["members"]:
            if not m.get("token") or not m.get("entry"):
                continue
            mint_grant(root, m["token"], meta.get("conn") or "",
                       revalidate=fingerprint_text(m.get("row_set")),
                       approver=approver, session=session, batch=token)
        mint_grant(root, token, meta.get("conn") or "", approver=approver,
                   session=session)
        return (f"deploy approved — {len(meta['members'])} statements, single "
                f"use, {GRANT_TTL_MINUTES} minutes. Run: {invocation()} deploy "
                f"{' '.join(meta['files'])} --confirm {token}")
    mint_grant(root, token, meta.get("connection") or "",
               revalidate=fingerprint_text(meta.get("row_set")),
               approver=approver, session=session)
    return (f"grant minted, single use, expires in {GRANT_TTL_MINUTES} "
            f"minutes. Run: {invocation()} apply <file> --confirm {token}")


# --- commands: autopilot ------------------------------------------------------

def _offline_connection(ns):
    """(name, entry) of the connection a command would use, resolved from the
    config without connecting — autopilot is bound to a connection by name."""
    cwd = pathlib.Path.cwd()
    cfg, root = find_config(cwd, os.environ)
    return resolve_connection(cfg, getattr(ns, "conn", None), os.environ,
                              cwd, root)


def door_lines(token, en=False):
    """The approval doors in the order an agent tries them; the first that
    works is the one, and a door that fails says so and names the next.
    Claude Code's own AskUserQuestion leads: it shows in every client
    (terminal, desktop, web, phone) and no hook or allow rule answers it.
    The MCP prompt follows — a host with no elicitation UI declines it
    unseen — and leads on Codex, which has no AskUserQuestion. A developer's
    terminal always works."""
    return [
        "    1. Claude Code:     AskUserQuestion, its text exactly the output of "
        + paint(f"{invocation()} approve --card {token}", "cyan", en)
        + ", options Approve / Reject",
        "    2. if that fails:   the " + paint("pythia_approve", "cyan", en)
        + f" tool (MCP) with token {token} — Codex starts here",
        "    3. last:            the developer runs "
        + paint(f"{invocation()} approve {token}", "cyan", en)
        + " in their own terminal (agents cannot run it)"]


def print_doors(token, en=False, then=None,
                head="The developer approves it once, the first door that works:"):
    print("\n  " + head)
    for ln in door_lines(token, en):
        print(ln)
    if then:
        print("    then the agent:     " + paint(then, "cyan", en))


def cmd_autopilot(conn, schema, ns):
    """Delegate a session once instead of approving every write. Requesting
    and revoking are anyone's; activating is the developer's act — approve
    the request's card through any door, or `autopilot on` at a console."""
    root = ns.project_root
    en = getattr(ns, "color", False)
    action = ns.action or "status"
    if action in ("request", "on"):
        name, entry = _offline_connection(ns)
        old = read_lease(root)
        groups = (parse_lease_groups(ns.groups) if ns.groups
                  else list((old or {}).get("groups") or LEASE_GROUPS_DEFAULT))
        req = new_lease_request(
            root, name, entry, groups=groups, hours=ns.hours,
            max_writes=ns.max_writes, destructive=ns.destructive,
            note=" ".join(ns.note or []))
        card = lease_card(req, root)
        if action == "on":
            if not console_session(ns):
                lease_request_file(root, req["token"]).unlink()
                sys.exit("autopilot on is the developer's command at a real "
                         "console. Agents: run `"
                         f"{invocation()} autopilot request` and ask with its "
                         "card — through the pythia_approve tool, "
                         "AskUserQuestion, or the developer's terminal.")
            lease = activate_lease(root, req, "console")
            if ns.json:
                print(json.dumps({"ok": True, "lease": lease}))
                return
            print()
            for ln in card:
                print(f"  {ln}")
            print(paint(f"\n  {lease_line(lease)}", "green", en))
            return
        if ns.json:
            print(json.dumps({"ok": True, "token": req["token"],
                              "card": f"{invocation()} approve {req['token']}\n"
                                      + "\n".join(card),
                              "request": req}))
            return
        print(f"\n  Autopilot request {paint(req['token'], 'bold', en)}:")
        for ln in card:
            print(f"  {ln}")
        print_doors(req["token"], en,
                    then=f"{invocation()} apply <file>   (no --confirm while "
                         "the lease lasts)")
        return
    if action == "off":
        lease = revoke_lease(root)
        if ns.json:
            print(json.dumps({"ok": True, "revoked": bool(lease)}))
            return
        print("Autopilot revoked — every write needs its own approval again."
              if lease else "No autopilot lease is active.")
        return
    if action == "report":
        token = ns.token
        lease = (read_lease(root) if not token else
                 _read_json(autopilot_root(root) / "history" / f"{token}.json")
                 or read_lease(root))
        if not lease:
            sys.exit("No autopilot lease on record.")
        rows = []
        for eid, m in lease_writes(root, lease.get("token")):
            own = [e for e in (m.get("compile_errors") or [])
                   if str(e[0]).upper() == str(m.get("object", "")).upper()]
            verdict = ("not written" if not m.get("applied") else
                       "BROKEN (exit 3)" if own or m.get("newly_invalid") else "ok")
            rows.append({"entry": eid, "object": m.get("object"),
                         "type": m.get("type"), "group": m.get("group"),
                         "verdict": verdict,
                         "restore": (f"{invocation()} journal restore {eid}"
                                     if m.get("applied") and m.get("group") in
                                     ("plsql_source", "comments") else None)})
        if ns.json:
            print(json.dumps({"lease": lease, "writes": rows}))
            return
        print(lease_line(lease))
        if not rows:
            print("-- no writes under this lease yet")
            return
        for r in rows:
            print(f"  {r['entry']:<46} {r['verdict']:<16} [{r['group']}]")
        undo = [r for r in rows if r["restore"]]
        if undo:
            print("\n-- to undo, newest first (each restore is itself a write "
                  "and goes through the gate):")
            for r in reversed(undo):
                print(f"     {r['restore']}")
        broken = [r for r in rows if r["verdict"].startswith("BROKEN")]
        if broken:
            print(paint(f"\n-- {len(broken)} write(s) ended BROKEN — report "
                        "them as broken, never as done.", "red", en))
        return
    lease = read_lease(root)
    pending = sorted((autopilot_root(root) / "requests").glob("*.json")) \
        if (autopilot_root(root) / "requests").is_dir() else []
    if ns.json:
        print(json.dumps({"lease": lease, "status": lease_status(lease),
                          "pending_requests": [p.stem for p in pending]}))
        return
    print(lease_line(lease))
    if pending:
        print(f"-- pending request(s) waiting for the developer: "
              f"{', '.join(p.stem for p in pending)}")


# --- commands: deploy ----------------------------------------------------------

DEPLOY_RANK = {"SEQUENCE": 10, "TABLE": 20, "INDEX": 30, "SYNONYM": 45,
               "TYPE": 50, "VIEW": 55, "MATERIALIZED VIEW": 56, "PACKAGE": 60,
               "FUNCTION": 70, "PROCEDURE": 70, "TYPE BODY": 75,
               "PACKAGE BODY": 80, "TRIGGER": 90}


def deploy_rank(text):
    """Where a statement belongs in a release: tables before the code that
    reads them, specs before bodies, triggers after both, grants and data
    last. Used only to order a directory nobody numbered."""
    made = created_object(text)
    if made:
        return DEPLOY_RANK.get(made[0], 60)
    group = classify(text)
    s = skip_leading_noise(text).lower()
    if s.startswith("alter table"):
        return 25
    return {"comments": 40, "grants": 100, "data_dml": 110,
            "session": 0}.get(group, 120)


def natural_key(name):
    """'2_x' before '10_x': numbers compare as numbers."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


NUMBERED_RE = re.compile(r"^[A-Za-z]{0,2}_{0,2}\d")     # 01_x, V3__x, R2_x


def collect_deploy(paths):
    """(files, statements, skipped, notes) for deploy: a directory expands to
    its .sql files (not recursive) — in natural name order when every file
    is numbered (01_, 1_, V3__: the author's order), else by what each file
    creates; files listed explicitly keep the order given. `notes` says which
    order each directory got, because a silent reordering is a surprise."""
    files, notes = [], []
    for raw in paths:
        p = pathlib.Path(raw)
        if p.is_dir():
            sub = sorted((x for x in p.iterdir()
                          if x.is_file() and x.suffix.lower() == ".sql"),
                         key=lambda f: natural_key(f.name))
            if sub and all(NUMBERED_RE.match(x.name) for x in sub):
                notes.append(f"{p}: by file name (numbered)")
            else:
                def first_rank(f):
                    st, _ = split_script(f.read_text(encoding="utf-8-sig"))
                    return (deploy_rank(st[0]["text"]) if st else 999,
                            natural_key(f.name))
                sub.sort(key=first_rank)
                if sub:
                    notes.append(f"{p}: by what each file creates (tables, "
                                 "specs, bodies, triggers, grants, data) — "
                                 "number the files to fix the order yourself")
            files.extend(sub)
        elif p.is_file():
            files.append(p)
        else:
            sys.exit(f"No such file or directory: {p}")
    if not files:
        sys.exit("Nothing to deploy: no .sql files found.")
    statements, skipped = [], []
    for f in files:
        st, sk = split_script(f.read_text(encoding="utf-8-sig"))
        for x in st:
            statements.append({"file": str(f), "line": x["line"],
                               "text": x["text"], "group": classify(x["text"])})
        skipped.extend({"file": str(f), **x} for x in sk)
    return files, statements, skipped, notes


def _first_line(text):
    s = skip_leading_noise(text)
    line = s.splitlines()[0] if s else ""
    return line if len(line) <= 70 else line[:67] + "..."


def _run_member(conn, schema, ns, st, confirm=None, preview=False, batch=None):
    """One statement through run_apply, captured: (code, refusal, docs). A
    driver error escaping run_apply's own handling becomes a refusal here, so
    a release never loses its summary after earlier statements landed."""
    import contextlib
    import copy
    import io
    sub = copy.copy(ns)
    sub.file, sub.json, sub.confirm = st["file"], True, confirm
    sub.yes, sub.preview, sub.batch = False, preview, batch
    buf = io.StringIO()
    err = None
    try:
        with contextlib.redirect_stdout(buf):
            code = run_apply(conn, schema, sub, st["text"])
    except SystemExit as e:
        code, err = (e.code if isinstance(e.code, int) else 1), (
            None if isinstance(e.code, int) else str(e))
    except Exception as e:                # noqa: BLE001 — reported, not lost
        code, err = 1, f"{type(e).__name__}: {e}"
    docs = []
    for ln in buf.getvalue().splitlines():
        if ln.strip().startswith("{"):
            try:
                docs.append(json.loads(ln))
            except ValueError:
                pass
    return code, err, docs


def _withdraw(root, batch, from_index):
    """A stopped deploy leaves no approval behind: the batch token is spent
    and every member grant not used yet is deleted."""
    for m in batch["members"][from_index:]:
        if m.get("token") and valid_token(m["token"]):
            try:
                grant_path(root, m["token"]).unlink()
            except OSError:
                pass
    spend_grant(root, batch["token"])


def cmd_deploy(conn, schema, ns):
    """Run a release — one or more scripts, many statements — through the same
    six steps apply runs, in order, stopping at the first refusal or broken
    result. Preview by default; one approval covers the whole ordered set; an
    autopilot lease that covers every statement lands it directly."""
    root = ns.project_root
    en = getattr(ns, "color", False)
    files, statements, skipped, notes = collect_deploy(ns.paths)
    start = max(1, int(getattr(ns, "from_n", 1) or 1))
    if start > len(statements):
        sys.exit(f"--from {start}: the scripts hold {len(statements)} statements.")
    statements = statements[start - 1:]
    if not statements:
        sys.exit("Nothing to deploy: the scripts hold no statements.")
    bad = [s for s in statements if s["group"] in (None, "anonymous", "session")]
    if bad:
        lines = [f"  {s['file']}:{s['line']} [{s['group'] or 'unrecognised'}] "
                 f"{_first_line(s['text'])}" for s in bad[:10]]
        sys.exit("Refused before anything ran — these statements cannot go "
                 "through the write path. Anonymous blocks and unrecognised "
                 "statements are refused as in apply; ALTER SESSION is refused "
                 "in a release because it would silently change how every "
                 "later statement runs, which no preview can show:\n"
                 + "\n".join(lines))
    eff = load_policy(root)
    lease = read_lease(root)
    conv = load_conventions(root)
    covered = []
    for s in statements:
        ok, _, why = autopilot_check(root, ns.conn_name, s["group"], s["text"])
        if ok:
            errs = [f for f in lint_findings(s["text"], s["group"], conv)
                    if f["severity"] == "error"]
            ok, why = (False, f"{len(errs)} lint error(s)") if errs else (ok, why)
        covered.append((ok, why))
    denied = [s for s, (ok, _) in zip(statements, covered)
              if eff[s["group"]][0] == "deny" and not ok
              and not lease_names_group(root, ns.conn_name, s["group"])]
    if denied:
        groups = sorted({s["group"] for s in denied})
        lines = [f"  {s['file']}:{s['line']} [{s['group']}] "
                 f"{_first_line(s['text'])}" for s in denied[:10]]
        sys.exit(f"Refused before anything ran: {', '.join(groups)} "
                 "is set to deny, and the release holds:\n" + "\n".join(lines)
                 + "\nThe developer decides — `"
                 + "`, `".join(f"{invocation()} policy set {g} confirm"
                               for g in groups)
                 + "` — or an autopilot lease that names the group.")
    auto_all = bool(lease) and all(ok for ok, _ in covered) and not ns.confirm \
        and not getattr(ns, "preview", False)

    if ns.confirm or auto_all:
        batch = None
        if ns.confirm:
            tok = str(ns.confirm).strip().lower()
            batch = (_read_json(batch_file(root, tok))
                     if re.fullmatch(r"[0-9a-f]{10}", tok) else None)
            if not batch:
                sys.exit(f"No deploy preview carries token {ns.confirm!r}. "
                         f"Preview first:\n  {invocation()} deploy "
                         f"{' '.join(ns.paths)}")
            if batch.get("applied"):
                sys.exit(f"Deploy {batch['token']} already ran — one approval, "
                         "one run. Preview again for a fresh token.")
            if [str(f) for f in files] != batch["files"] or \
                    int(batch.get("from") or 1) != start:
                sys.exit("The approved deploy listed different files or started "
                         "elsewhere:\n  " + " ".join(batch["files"])
                         + f" (from statement {batch.get('from') or 1})\n"
                         "Preview again with the files you mean to run.")
            # every statement exactly as previewed, checked BEFORE the first
            # one runs — a change found at statement 5 would be found after
            # statements 1-4 had already landed
            moved = [i + start for i, (m, st) in enumerate(
                zip(batch["members"], statements))
                if m.get("sha") != source_sha(st["text"])]
            if len(batch["members"]) != len(statements) or moved:
                sys.exit("The scripts changed since that preview"
                         + (f" (statement {moved[0]} first)" if moved else
                            " (the statement count moved)")
                         + ". Nothing ran. Preview again.")
            g = read_grant(root, batch["token"])
            status = grant_status(g, ns.conn_name)
            if status != "ok":
                sys.exit(grant_refusal(status, batch["token"], g or {},
                                       ns.conn_name, " ".join(ns.paths))
                         .replace(" apply ", " deploy "))
        results, code, stop = [], 0, None
        for i, st in enumerate(statements):
            member = batch["members"][i] if batch else None
            c, err, docs = _run_member(
                conn, schema, ns, st, confirm=(member or {}).get("token") or None,
                batch=(batch or {}).get("token"))
            last = docs[-1] if docs else {}
            applied = bool(last.get("applied"))
            res = {"n": start + i, "file": st["file"], "line": st["line"],
                   "group": st["group"],
                   "object": last.get("object") or (docs[0].get("object") if docs else None),
                   "exit": c if not err else 1, "refusal": err,
                   "applied": applied, "errors": last.get("errors"),
                   "newly_invalid": last.get("newly_invalid"),
                   "restore": last.get("restore")}
            if not err and not applied and c == 0:
                res["exit"], res["refusal"] = 1, ((docs[0].get("autopilot") or {})
                                                  .get("why") if docs else None) \
                    or "not applied (no approval covered it)"
            results.append(res)
            if res["exit"]:
                code, stop = res["exit"], start + i
                break
        if batch:
            if stop is None:
                batch["applied"] = True
                _write_json(batch_file(root, batch["token"]), batch)
                spend_grant(root, batch["token"])
            else:
                _withdraw(root, batch, len(results))
        if ns.json:
            print(json.dumps({"ok": stop is None,
                              "mode": "autopilot" if auto_all else "confirm",
                              "batch": (batch or {}).get("token"),
                              "results": results, "stopped_at": stop,
                              "not_run": len(statements) - len(results),
                              "exit": code}))
        else:
            print(f"\n  deploy — {len(statements)} statements"
                  + (" (autopilot)" if auto_all else ""))
            for r in results:
                tag = (paint("ok", "green", en) if not r["exit"] else
                       paint("BROKEN (exit 3)", "red", en) if r["exit"] == 3
                       else paint("REFUSED", "red", en))
                text = statements[r["n"] - start]["text"]
                print(f"  {r['n']:>3}. {tag:<10} [{r['group']}] "
                      f"{r['object'] if r['object'] not in (None, 'STATEMENT') else _first_line(text)}"
                      f"   {r['file']}:{r['line']}")
                if r["refusal"]:
                    for ln in str(r["refusal"]).splitlines()[:6]:
                        print(f"         {ln}")
                for e in r.get("errors") or []:
                    print(paint(f"         {e[3]}:{e[4]} {e[5]} {str(e[6]).strip()}",
                                "red", en))
                for n2, t2 in r.get("newly_invalid") or []:
                    print(paint(f"         now INVALID: {n2} ({t2})", "red", en))
            if stop is not None:
                print(paint(f"\n  Stopped at statement {stop}; "
                            f"{len(statements) - len(results)} not run. The "
                            "rest of this approval is withdrawn.", "red", en))
                print(f"  After fixing it, resume with a fresh preview:\n    "
                      + paint(f"{invocation()} deploy {' '.join(ns.paths)} "
                              f"--from {stop}", "cyan", en))
            undo = [r["restore"] for r in results if r.get("restore") and r["applied"]]
            if undo:
                print("\n  To undo what landed, newest first:")
                for u in reversed(undo):
                    print("    " + paint(u, "cyan", en))
            nolift = [r for r in results if r["applied"] and r["group"]
                      not in ("plsql_source", "comments")]
            if nolift:
                print(paint(f"  {len(nolift)} applied statement(s) "
                            "(structural/data/grants) have no undo.",
                            "yellow", en))
        if code:
            sys.exit(code)
        return

    # preview: every statement previewed, then one card for the whole set
    members, previews = [], []
    for i, st in enumerate(statements):
        c, err, docs = _run_member(conn, schema, ns, st, preview=True)
        if err or c:
            hint = ""
            if st["group"] == "data_dml" and any(
                    x["group"] == "structural" for x in statements[:i]):
                hint = ("\nIt probably reads a table an earlier statement of "
                        "this release creates or changes, which has not run "
                        "yet. Deploy the DDL first, then this part with "
                        f"--from {start + i}.")
            sys.exit(f"Preview stopped at {st['file']}:{st['line']} — nothing "
                     f"was written:\n{err or 'exit ' + str(c)}{hint}")
        d = docs[0] if docs else {}
        members.append({"n": start + i, "file": st["file"], "line": st["line"],
                        "group": st["group"], "object": d.get("object"),
                        "type": d.get("type"), "token": d.get("token"),
                        "entry": d.get("journal"),
                        "changed": d.get("changed_lines"),
                        "created": d.get("created"),
                        "summary": d.get("summary"),
                        "destructive": d.get("destructive"),
                        "row_set": d.get("row_set"),
                        "lint_errors": sum(1 for f in d.get("lint") or []
                                           if f["severity"] == "error"),
                        "statement": (None if st["group"] == "plsql_source"
                                      else skip_leading_noise(st["text"])),
                        "sha": source_sha(st["text"]),
                        "first_line": _first_line(st["text"])})
        previews.append(d)
    batch = write_batch(root, ns.conn_name, schema, [str(f) for f in files],
                        members, start=start)
    if ns.json:
        print(json.dumps({"ok": True, "mode": "preview", "batch": batch["token"],
                          "members": members, "previews": previews,
                          "skipped": skipped, "order": notes}))
        return
    print(f"\n  {paint('deploy preview', 'bold', en)} — nothing was written. "
          f"{len(members)} statements from {len(files)} file(s).")
    for n in notes:
        print(f"  order: {n}")
    print()
    for ln in batch_card(batch):
        print(f"  {paint(ln, 'yellow', en) if 'DESTRUCTIVE' in ln else ln}")
    for m, d in zip(members, previews):
        lint = d.get("lint") or []
        if lint:
            print(f"\n  {m['n']}. lint: {lint_summary(lint)}")
            for ln in render_findings(lint, label=f"{m['file']}", en=en, limit=5):
                print(f"    {ln}")
        if d.get("drift"):
            print(paint(f"\n  {m['n']}. ! {d['drift'].splitlines()[0]}",
                        "yellow", en))
        if m["entry"] and m["group"] == "plsql_source":
            e = read_journal_entry(root, m["entry"])
            base = (create_prefix(e["after"]) + e["before"]) if e["before"].strip() else ""
            dl = render_diff(base, e["after"])[0].splitlines()
            print(f"\n  {m['n']}. diff {m['object']}:")
            for ln in dl[:40]:
                print(f"    {paint_diff_line(ln, en)}")
            if len(dl) > 40:
                print(f"    -- truncated: {len(dl) - 40} more diff lines; "
                      f"{invocation()} journal diff {m['entry']}")
    for sk in skipped:
        print(paint(f"  skipped {sk['file']}:{sk['line']} {sk['text']!r} — "
                    f"{sk['why']}", "dim", en))
    if lease:
        nc = [(m, why) for m, (ok, why) in zip(members, covered) if not ok]
        if nc:
            print(paint(f"\n  ! autopilot does not cover {len(nc)} of these — "
                        f"first: statement {nc[0][0]['n']}: {nc[0][1]}",
                        "yellow", en))
    from_arg = f" --from {start}" if start > 1 else ""
    print_doors(batch["token"], en,
                then=f"{invocation()} deploy {' '.join(ns.paths)}{from_arg} "
                     f"--confirm {batch['token']}")


# --- commands: lint -------------------------------------------------------------

def cmd_lint(conn, schema, ns):
    """Check files against the built-in standards and the LEAD's rules in
    .pythia/conventions.json. No database. Exit 2 when any finding is an
    error — the verdict, like apply's exit codes."""
    conv = load_conventions(ns.project_root)
    en = getattr(ns, "color", False)
    report, errors = {}, 0
    paths = []
    for raw in ns.paths:
        p = pathlib.Path(raw)
        paths.extend(sorted(p.rglob("*.sql")) if p.is_dir() else [p])
    for p in paths:
        if not p.is_file():
            sys.exit(f"No such file: {p}")
    texts = {p: p.read_text(encoding="utf-8-sig") for p in paths}
    in_set = None
    if len(paths) > 1:
        in_set = set()
        for text in texts.values():
            in_set |= script_comment_targets(text)
    for p in paths:
        f = lint_script(texts[p], conv, in_set)
        report[str(p)] = f
        errors += sum(1 for x in f if x["severity"] == "error")
    # one file, comments missing: they may live in another file of the same
    # change, which only one run over the set can count (field test 0.15.6)
    hint = None
    if len(paths) == 1 and any(x["rule"] == "comment-missing"
                               for x in report[str(paths[0])]):
        hint = (f"COMMENT ON in another file? Comments count across the files "
                f"of one run: {invocation()} lint {paths[0]} <its comments "
                "file> (or the folder).")
    if ns.json:
        out = {"files": report, "errors": errors,
               "rules": {k: v[0] for k, v in LINT_RULES.items()}}
        if hint:
            out["hint"] = hint
        print(json.dumps(out))
    else:
        for path, f in report.items():
            for ln in render_findings(f, label=path, en=en):
                print(ln)
        total = [x for f in report.values() for x in f]
        print(f"-- {len(report)} file(s): {lint_summary(total)}"
              + ("" if conv and conv.get("lint") else
                 "  (built-in rules; the LEAD tunes them in "
                 ".pythia/conventions.json \"lint\")"))
        if hint:
            print("-- " + hint)
    if errors:
        sys.exit(2)


# --- commands: explain, stats, comments -----------------------------------------

def plan_notes(lines):
    """What a reader of the plan should not miss — heuristics, said as such."""
    text = "\n".join(lines)
    notes = []
    fulls = sorted(set(re.findall(r"TABLE ACCESS (?:STORAGE )?FULL\s*\|\s*([\w$#]+)", text)))
    if fulls:
        notes.append(f"full scan of {', '.join(fulls)} — fine for small tables "
                     "or most of the rows; otherwise check the predicate is "
                     f"indexable (`{invocation()} stats <TABLE>`)")
    if "CARTESIAN" in text:
        notes.append("MERGE JOIN CARTESIAN — usually a missing join predicate")
    if re.search(r"INTERNAL_FUNCTION\(|TO_NUMBER\(\"|TO_CHAR\(\"|SYS_OP_C2C", text):
        notes.append("an implicit conversion sits on a column in the "
                     "predicates — it disables the index; compare like types")
    if re.search(r"dynamic (statistics|sampling) used", text, re.I):
        notes.append("dynamic sampling was used: statistics are missing or "
                     "stale for a table here")
    if re.search(r"FILTER\s*\|", text) and re.search(r"\bNOT IN\b", text, re.I):
        notes.append("FILTER with NOT IN: a nullable column can turn this into "
                     "a row-by-row probe — NOT EXISTS is safer")
    return notes


def cmd_explain(conn, schema, ns):
    """The optimizer's plan for a query — EXPLAIN PLAN + DBMS_XPLAN.DISPLAY,
    license-safe (no V$/AWR/ASH). The statement is parsed, never run."""
    import secrets
    if ns.file:
        text = pathlib.Path(ns.file).read_text(encoding="utf-8-sig")
    else:
        text = " ".join(ns.statement or [])
    text = text.strip().rstrip(";").strip()
    if not text:
        sys.exit(f"Usage: {invocation()} explain \"select ...\" | --file q.sql")
    if not is_readonly_sql(skip_leading_noise(text)):
        sys.exit("explain takes a SELECT/WITH query. For DML, explain the "
                 "SELECT that finds its rows.")
    sid = "PYTHIA_" + secrets.token_hex(4)
    # interpolated, not bound: EXPLAIN PLAN takes no binds for its statement,
    # and the id is ours (hex). The query is parsed by the optimizer, never
    # executed; the PLAN_TABLE rows it writes are rolled back below.
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{sid}' FOR {text}")
        cols, rows = run_query(conn, load_query("explain-display.sql"),
                               {"id": sid, "fmt": ns.format})
    finally:
        try:
            conn.rollback()
        except Exception:                 # noqa: BLE001 — cleanup only
            pass
    lines = [str(cell(r[0])) for r in rows]
    notes = plan_notes(lines)
    if ns.json:
        print(json.dumps({"plan": lines, "notes": notes}))
        return
    for ln in lines:
        print(ln)
    for n in notes:
        print(f"-- note: {n}")


def cmd_stats(conn, schema, ns):
    """Optimizer statistics and indexes of one table — what tuning starts
    from. Unindexed foreign keys are listed because they lock."""
    out = {}
    for key, q in (("table", "table-stats.sql"), ("indexes", "table-indexes.sql"),
                   ("unindexed_fks", "unindexed-fks.sql")):
        cols, rows = run_query(conn, load_query(q), {"s": schema, "n": ns.name})
        out[key] = (cols, rows)
    if not out["table"][1]:
        sys.exit(f"No table {ns.name.upper()} in {schema}.")
    if ns.json:
        print(json.dumps({k: [dict(zip(c, map(cell, r))) for r in rows]
                          for k, (c, rows) in out.items()}))
        return
    for key, (cols, rows) in out.items():
        print(f"-- {key.replace('_', ' ')}")
        emit_table(ns, cols, rows, False)
    t = dict(zip(out["table"][0], out["table"][1][0]))
    if not t.get("LAST_ANALYZED"):
        print("-- note: never analyzed — the optimizer is guessing; "
              "DBMS_STATS.GATHER_TABLE_STATS is the DBA's or the owner's call")
    elif str(t.get("STALE_STATS") or "").upper() == "YES":
        print("-- note: statistics are STALE")
    if out["unindexed_fks"][1]:
        print("-- note: unindexed foreign keys lock this table on parent "
              "DELETE/UPDATE of the key")


def cmd_comments(conn, schema, ns):
    """The table's comment and every column's — and which are missing.
    --scaffold prints the COMMENT statements to fill in, one per line."""
    cols, rows = run_query(conn, load_query("table-comments.sql"),
                           {"s": schema, "n": ns.name})
    if not rows:
        sys.exit(f"No table or view {ns.name.upper()} in {schema}.")
    missing = [r for r in rows if not (cell(r[3]) or "").strip()]
    if getattr(ns, "scaffold", False):
        for r in missing:
            kind = "table" if r[1] is None else "column"
            print(render_comment(kind, cell(r[0]), cell(r[1]) if r[1] else None,
                                 "").rstrip() + ";")
        return
    if ns.json:
        print(json.dumps({"rows": [dict(zip(cols, map(cell, r))) for r in rows],
                          "missing": len(missing)}))
        return
    emit_table(ns, cols, rows, False)
    print(f"-- {len(missing)} of {len(rows)} without a comment"
          + (f"; {invocation()} comments {ns.name} --scaffold writes the "
             "statements to fill in" if missing else ""))


# --- commands: hooks — the harness, enforced by the client ---------------------
#
# Claude Code runs these. pre-tool (PreToolUse, matchers Bash and SQLcl's
# run-sql) lets pythia's provably read-only commands through without a prompt
# that carries no decision, lets an approved or lease-covered write through,
# and denies writes that would skip the gate (sqlplus/SQLcl DDL, a forged
# approve --hook). post-tool (PostToolUse on Bash) puts the verdict rules next
# to an apply result the moment it lands. session-start prints the harness and
# the project's live state. Every one of them fails silent: a hook that crashes
# must never block the developer's work.

PYTHIA_READS = {"check", "ls", "src", "args", "ddl", "cols", "grep", "sql",
                "invalid", "errors", "deps", "impact", "similar", "plscope",
                "history", "unistr", "connections", "guide", "doctor", "lint",
                "explain", "stats", "comments"}

SQL_CLIENT_RE = re.compile(
    r"(?:^|[;&|(]\s*|\b(?:then|do|exec)\s+)(?:\S*[/\\])?(sqlplus|sqlcl|sql)"
    r"(?:\.exe)?(?=\s|$)", re.I | re.M)
WRITE_SQL_RE = re.compile(
    r"\b(create\s+(or\s+replace\s+)?((non?)?editionable\s+|force\s+|global\s+"
    r"temporary\s+|unique\s+|bitmap\s+)*(procedure|function|package|trigger|"
    r"type|view|table|index|sequence|synonym|materialized)|alter\s+(table|"
    r"index|sequence|procedure|package|function|trigger|view|type|user)|"
    r"drop\s+(table|view|index|sequence|procedure|package|function|trigger|"
    r"type|synonym|user|materialized)|truncate\s+table|insert\s+into|"
    r"update\s+[\w$#\".]+\s+(\w+\s+)?set|delete\s+from|merge\s+into|"
    r"grant\s+\w+|revoke\s+\w+|comment\s+on)\b", re.I)


def _is_real_pythia(path_token):
    """Whether a path token names THIS pythia — the module running the hook —
    and not a file an agent wrote that happens to be called pythia.py."""
    try:
        return pathlib.Path(path_token).resolve() == pathlib.Path(__file__).resolve()
    except (OSError, ValueError):
        return False


def parse_pythia_command(command, cwd=None):
    """The argv after the invocation for ONE plain pythia command — no
    operators, no substitutions, no newlines, no comments — else None.
    Allowing a command means allowing all of it, so anything a shell could
    turn into a second command is refused here and left to the client's own
    prompt. So is any executable that might not be the real pythia: a bare
    `pythia` from PATH, an interpreter by name with `-m pythia` (and no
    pythia.py in the working directory to shadow it), or a path to the very
    file running this hook."""
    import shlex
    if not command or any(ch in command for ch in "\n\r`#") or re.search(
            r"\$\(|\$\{|<\(|>\(", command):
        return None                       # '#' too: bash and shlex disagree on it
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        toks = list(lex)
    except ValueError:
        return None
    if not toks or any(t and all(c in "();<>|&" for c in t) for t in toks):
        return None
    first = toks[0]
    exe = re.sub(r"\.exe$", "", first.lower())
    if exe == "pythia":                   # by name only: PATH decides, not the agent
        return toks[1:]
    if _is_abs_path(first) and sys.executable:
        # this hook's own interpreter by absolute path — the spelling that
        # runs where pythia's venv is not on PATH (field test 0.15.4) — or
        # the pythia script beside it; any other path is a stranger's file
        own = pathlib.Path(sys.executable)
        if _same_path(first, own) and toks[1:3] == ["-m", "pythia"]:
            base = pathlib.Path(cwd or os.getcwd())
            if (base / "pythia.py").exists() or (base / "pythia").is_dir():
                return None               # python -m would import that one
            return toks[3:]
        if _same_path(first, own) and len(toks) > 1 and _is_real_pythia(
                pathlib.Path(cwd or os.getcwd()) / toks[1]):
            return toks[2:]               # a clone's launcher: this very file
        if any(_same_path(first, own.with_name(n))
               for n in ("pythia", "pythia.exe")):
            return toks[1:]
        return None
    if re.fullmatch(r"python(\d+(\.\d+)?)?|py", exe):
        if toks[1:3] == ["-m", "pythia"]:
            base = pathlib.Path(cwd or os.getcwd())
            if (base / "pythia.py").exists() or (base / "pythia").is_dir():
                return None               # python -m would import that one
            return toks[3:]
        if len(toks) > 1 and _is_real_pythia(
                pathlib.Path(cwd or os.getcwd()) / toks[1]):
            return toks[2:]
    return None


def classify_pythia_argv(argv):
    """read | preview | write | approve-hook | other — decided by parsing argv
    with the CLI's own parser, so an option value can never pass for a
    subcommand action and an abbreviation never hides a flag. Returns
    (kind, namespace|None)."""
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            ns = build_parser().parse_args(argv)
    except SystemExit as e:
        # exit 0 inside the parser is --help: it prints and stops before
        # main() runs any command (field test 0.15.6). Errors exit 2.
        return ("read" if e.code == 0 else "other"), None
    sub = ns.command
    if getattr(ns, "yes", False):
        return "other", ns
    if sub == "approve":
        if ns.hook:
            return "approve-hook", ns
        return ("read" if ns.card else "other"), ns
    if sub in PYTHIA_READS:
        return "read", ns
    if sub == "conventions":
        return ("other" if ns.init else "read"), ns
    if sub == "policy":
        return ("read" if ns.action == "show" else "other"), ns
    if sub == "journal":
        if ns.action in ("list", "show", "diff", "export"):
            return "read", ns
        if ns.action == "restore":
            return ("write" if ns.confirm else "preview"), ns
        return "other", ns
    if sub == "autopilot":
        return ("read" if ns.action in ("status", "report", "request", "off")
                else "other"), ns
    if sub in ("apply", "deploy"):
        return ("write" if ns.confirm else "preview"), ns
    if sub == "skill":
        return ("read" if ns.action == "lint" else "other"), ns
    return "other", ns


def _hook_out(event, decision=None, reason=None, context=None):
    out = {"hookEventName": event}
    if decision:
        out["permissionDecision"] = decision
        out["permissionDecisionReason"] = reason or ""
    if context:
        out["additionalContext"] = context
    return {"hookSpecificOutput": out}


def raw_write_bypass(command):
    """A reason to deny, when a shell command writes to Oracle around the gate."""
    if not SQL_CLIENT_RE.search(command or ""):
        return None
    if WRITE_SQL_RE.search(command) or re.search(r"(^|\s)@@?\S", command):
        return ("Writes go through `pythia apply <file>` (one statement) or "
                "`pythia deploy <files>` (a script): snapshot, preview, "
                "approval, verify, journal. sqlplus/SQLcl here would write "
                "with none of them (AGENTS.md rule 3). Reads: `pythia sql "
                "\"select ...\"`. If the developer wants to run it by hand, "
                "hand them the file — and the rollback from `pythia history`.")
    return None


def pre_tool_decision(payload, now=None):
    """The PreToolUse verdict for one tool call, or None to stay out of it.
    Quiet on stderr: config warnings belong to the commands, not to a hook
    that runs before every Bash call."""
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        return _pre_tool_decision(payload, now)


def _pre_tool_decision(payload, now=None):
    if not isinstance(payload, dict):
        return None
    tool = str(payload.get("tool_name") or "")
    tin = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    mode = payload.get("permission_mode") or "default"
    cwd = pathlib.Path(payload.get("cwd") or os.getcwd())
    _, root = find_config(cwd, os.environ)
    root = root or cwd
    settings = load_settings(root) if (pathlib.Path(root) / CONFIG_DIR /
                                       "settings.json").is_file() else {}
    if tool == "Bash":
        cmd = str(tin.get("command") or "")
        if settings.get("block_raw_writes", True):
            why = raw_write_bypass(cmd)
            if why:
                return _hook_out("PreToolUse", "deny", why)
        if re.search(r"\bapprove\b[^\n;&|]*--hook\b", cmd) and "pythia" in cmd:
            return _hook_out("PreToolUse", "deny",
                             "approve --hook is the client's door: it reads a "
                             "payload Claude Code wrote. Piping one in by hand "
                             "would forge the developer's approval.")
        argv = parse_pythia_command(cmd, cwd)
        if argv is None or not settings.get("auto_allow_reads", True):
            return None
        kind, pns = classify_pythia_argv(argv)
        if kind == "read":
            return _hook_out("PreToolUse", "allow",
                             "pythia read-only command — it cannot write")
        if mode == "plan" or kind not in ("preview", "write"):
            return None
        conn_name = getattr(pns, "conn", None)
        if not conn_name:
            try:
                cfg, r2 = find_config(cwd, os.environ)
                conn_name = resolve_connection(cfg, None, os.environ, cwd, r2)[0]
            except SystemExit:
                conn_name = None
        lease = read_lease(root)
        lease_ok = (lease_status(lease, now) == "active" and conn_name and
                    str(lease.get("conn", "")).upper() == conn_name.upper())
        if kind == "preview":
            return _hook_out("PreToolUse", "allow",
                             "pythia preview — " + (
                                 "applies only what the developer's autopilot "
                                 "lease covers; apply re-checks the scope"
                                 if lease_ok else "writes nothing to the database"))
        token = str(getattr(pns, "confirm", "") or "").strip().lower()
        g = read_grant(root, token) if token else None
        # a grant bound to a deploy batch opens only that deploy
        bound_ok = not (g or {}).get("batch") or pns.command == "deploy"
        if g and bound_ok and grant_status(g, conn_name or g.get("conn"), now) == "ok":
            return _hook_out("PreToolUse", "allow",
                             f"the developer approved {token} "
                             f"({g.get('approver')}); apply re-verifies it")
        if lease_ok:
            return _hook_out("PreToolUse", "allow",
                             "the developer's autopilot lease covers this "
                             "connection; apply re-checks the scope")
        return None
    if re.search(r"run[-_]?sql", tool, re.I) and settings.get("block_raw_writes", True):
        text = " ".join(str(v) for v in tin.values())
        if WRITE_SQL_RE.search(text):
            return _hook_out("PreToolUse", "deny",
                             "Writes go through `pythia apply` / `pythia deploy` "
                             "— snapshot, preview, approval, verify, journal. "
                             "The SQLcl MCP server is for reads here "
                             "(AGENTS.md rule 3).")
    return None


def post_tool_note(payload):
    """Context for the agent right after a pythia write result lands."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    tin = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    cmd = str(tin.get("command") or "")
    if not re.search(r"pythia\S*\s+(?:[^|;&]*\s)?(apply|deploy|restore)\b", cmd):
        return None
    resp = payload.get("tool_response")
    out = (" ".join(str(resp.get(k) or "") for k in ("stdout", "stderr", "output"))
           if isinstance(resp, dict) else str(resp or ""))
    notes = []
    if re.search(r"pythia[^|]*\|\s*\S", cmd):
        notes.append("This was piped: the exit code you see is the pipe's, not "
                     "pythia's (rule 6). Read pythia's own verdict lines, or "
                     "rerun without the pipe.")
    if re.search(r"did not compile cleanly|broke other objects|BROKEN \(exit 3\)|\"exit\": 3", out):
        notes.append("EXIT 3 — written but BROKEN. Never report this as done: "
                     "show the compile errors / newly INVALID objects and the "
                     "printed restore command, then let the developer choose "
                     "fix-forward or restore.")
    if "FAILED — nothing was written" in out:
        notes.append("Nothing was written and the approval is unspent; fix the "
                     "cause, then preview again if the file changed.")
    if re.search(r"^Refused|Refused before anything ran|^\s*REFUSED", out, re.M):
        notes.append("A refusal is information for the developer: relay the "
                     "reason and its printed fix; do not route around it.")
    if "Autopilot stopped itself" in out:
        notes.append("Autopilot paused itself — stop and show the developer "
                     "this result before anything else.")
    if not notes:
        return None
    return _hook_out("PostToolUse", context="pythia: " + " ".join(notes))


def session_state_lines(root, home=None):
    """The project's live state, for the session preamble — no database."""
    lines = []
    run = invocation()
    cfg_path = pathlib.Path(root) / CONFIG_DIR / CONFIG_NAME
    if cfg_path.is_file():
        try:
            rows = connection_summary(json.loads(cfg_path.read_text(encoding="utf-8")))
        except ValueError:
            rows = []
        names = ", ".join(r["name"] + ("*" if r["default"] else "") for r in rows)
        lines.append(f"connections: {names or 'none filled in'} (* = default; "
                     f"`{run} connections` for details)")
    else:
        lines.append(f"connections: none — `{run} install` scaffolds "
                     ".pythia/connections.json")
    lines.append(lease_line(read_lease(root)))
    if (pathlib.Path(root) / CONFIG_DIR / "conventions.md").is_file():
        lines.append("conventions: .pythia/conventions.md exists — read it "
                     "before writing any object")
    mcp = _read_json(pathlib.Path(root) / ".mcp.json") or {}
    # a venv install registers the approver in Claude Code's local scope
    # (~/.claude.json), never in the shared .mcp.json
    has_mcp = ("pythia" in (mcp.get("mcpServers") or {})
               or local_mcp_entry(root, home) is not None)
    # the order is what works here, not a guess: Claude Code's own dialog
    # leads where pythia's answer hook is wired (it shows in every client),
    # the MCP prompt follows (some hosts decline it unseen), the terminal is
    # last; dontAsk denies both chat doors. A scan that trips over a file
    # must not cost the session its guide.
    try:
        hook_wired = approve_hook_wired(root, home)
        dont_ask = any(what == DONTASK_NOTE
                       for _, what in ask_question_breakers(root, home))
    except Exception:                     # noqa: BLE001
        hook_wired, dont_ask = False, False
    terminal = f"`{run} approve <token>` in the developer's terminal"
    if dont_ask:
        lines.append("approval door: dontAsk mode denies AskUserQuestion and "
                     f"pythia_approve here — relay {terminal}")
    elif hook_wired:
        lines.append("approval door: AskUserQuestion, its text exactly the "
                     f"output of `{run} approve --card <token>` (options Approve "
                     "/ Reject) — Claude Code's own dialog, shown in every "
                     "client; if it fails, "
                     + ("the pythia_approve tool (MCP); last, " if has_mcp else "")
                     + f"{terminal}. When a door fails, go to the next without "
                     "asking")
    elif has_mcp:
        lines.append("approval door: the pythia_approve tool (MCP) — pythia's "
                     "AskUserQuestion hook is not wired here; if the tool "
                     "reports the client did not show its card, relay "
                     + terminal)
    return lines


def cmd_hook(conn, schema, ns):
    """Entry point for client hooks. Reads the hook payload on stdin."""
    event = ns.event
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        payload = {}
    try:
        if event == "session-start":
            root = ns.project_root
            if isinstance(payload, dict) and payload.get("cwd"):
                _, r2 = find_config(pathlib.Path(payload["cwd"]), {})
                root = r2 or root
            md = pathlib.Path(root) / "CLAUDE.md"
            text = md.read_text(encoding="utf-8", errors="replace") \
                if md.is_file() else ""
            loaded = AGENTS_BEGIN in text and AGENTS_END in text
            run = invocation()
            if loaded and squash(claude_md_block()) in squash(text):
                # Claude Code already loaded the guide from CLAUDE.md: the
                # hook adds what a static file cannot — the live state
                print("PYTHIA HARNESS ACTIVE - the rules are in CLAUDE.md "
                      f"(pythia block); `{run} guide` prints the full contract.")
            else:
                head, rest = BRIEF_GUIDE.rstrip().split("\n", 1)
                print(head)
                if loaded:
                    # upgraded, not re-installed: the block carries an older
                    # version's rules (the door order among them) — said
                    # under the one header, before the rules that replace it
                    print("NOTE   CLAUDE.md's pythia block is from an older "
                          "pythia; where it differs from the rules below, these "
                          f"win. `{run} install` refreshes it.")
                print(rest)
            if run not in ("pythia", "pythia.exe"):
                # the shared files say `pythia`; this machine runs it otherwise
                # (a venv nobody activated, a clone) — field test 0.15.4
                # ...typed as written: a shell variable or a `;` makes it a
                # compound command, which the guard leaves to the client's
                # prompt (field test 0.15.5: PY=...; "$PY" -m pythia ...)
                print(f"RUN    `pythia` here is `{run}` — spell every pythia "
                      "command the rules name that way, as written, one command "
                      "per call: a shell variable or `;` makes it a compound "
                      "command the guard asks about.")
            print("STATE  " + "\n       ".join(session_state_lines(root)))
            return
        out = (pre_tool_decision(payload) if event == "pre-tool"
               else post_tool_note(payload) if event == "post-tool" else None)
    except SystemExit:
        return                            # a config problem never blocks a tool
    except Exception:                     # noqa: BLE001 — fail silent, always
        return
    if out:
        print(json.dumps(out))


# --- commands: doctor — is the harness wired, and what will break it ------------

def _settings_files(project_root, home=None):
    home = pathlib.Path(home or pathlib.Path.home())
    return [pathlib.Path(project_root) / ".claude" / "settings.json",
            pathlib.Path(project_root) / ".claude" / "settings.local.json",
            home / ".claude" / "settings.json",
            home / ".claude" / "settings.local.json"]


def _matcher_hits(matcher, tool):
    if matcher in (None, "", "*"):
        return True
    if matcher == tool:
        return True
    try:
        return bool(re.fullmatch(matcher, tool))
    except re.error:
        return False


def _frontmatter_value(text, key):
    m = re.match(r"^---\n(.*?)\n---\n", text.replace("\r\n", "\n"), re.S)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.split(":", 1)[0].strip() == key and ":" in line:
            return line.split(":", 1)[1].strip()
    return None


def approve_hook_wired(project_root, home=None):
    """Is pythia's AskUserQuestion answer hook (`approve --hook`) wired for
    this project — in its settings, the user's, or the enabled pythia
    plugin? Without it a chat answer mints nothing."""
    home = pathlib.Path(home or pathlib.Path.home())
    root = pathlib.Path(project_root)
    blobs = [json.dumps((_read_json(f) or {}).get("hooks") or {})
             for f in (root / ".claude" / "settings.json",
                       root / ".claude" / "settings.local.json",
                       home / ".claude" / "settings.json",
                       home / ".claude" / "settings.local.json")]
    blobs += [json.dumps(h) for _, _, h in enabled_plugin_hooks(root, home)]
    return any("approve --hook" in b for b in blobs)


def enabled_plugin_hooks(project_root, home=None, env=None):
    """[(plugin, where, hooks)] for every plugin Claude Code runs here: each
    one installed_plugins.json lists (else each install under cache/) that
    enabledPlugins does not switch off — user, then project, then local
    settings, the last mention winning; no mention means on, unless its
    plugin.json says defaultEnabled: false. Hooks come from hooks/hooks.json
    and from plugin.json's `hooks` (paths, inline objects, or a list of
    both). A marketplace clone holds the source of every plugin it lists,
    installed or not, so it is never scanned: only an install path is."""
    home = pathlib.Path(home or pathlib.Path.home())
    env = os.environ if env is None else env
    root = pathlib.Path(project_root)
    state = {}
    for path in (home / ".claude" / "settings.json",
                 root / ".claude" / "settings.json",
                 root / ".claude" / "settings.local.json"):
        ep = (_read_json(path) or {}).get("enabledPlugins")
        if isinstance(ep, dict):
            for key, on in ep.items():
                state[str(key)] = on is True
    plugins = pathlib.Path(env.get("CLAUDE_CODE_PLUGIN_CACHE_DIR")
                           or home / ".claude" / "plugins")
    installs = {}
    listed = (_read_json(plugins / "installed_plugins.json") or {}).get("plugins")
    if isinstance(listed, dict):
        for key, entries in listed.items():
            for e in entries if isinstance(entries, list) else [entries]:
                if isinstance(e, dict) and isinstance(e.get("installPath"), str):
                    installs.setdefault(str(key), []).append(pathlib.Path(e["installPath"]))
    elif (plugins / "cache").is_dir():    # no list: the newest copy in the cache
        try:
            for mkt in sorted((plugins / "cache").iterdir()):
                for plug in sorted(mkt.iterdir()) if mkt.is_dir() else []:
                    versions = [d for d in plug.iterdir() if d.is_dir()] \
                        if plug.is_dir() else []
                    if versions:
                        installs[f"{plug.name}@{mkt.name}"] = [
                            max(versions, key=lambda d: d.stat().st_mtime)]
        except OSError:
            pass
    out = []
    for key, roots in installs.items():
        for pr in roots:
            manifest = _read_json(pr / ".claude-plugin" / "plugin.json") or {}
            if not state.get(key, manifest.get("defaultEnabled", True) is not False):
                continue
            found = [(pr / "hooks" / "hooks.json", None)]
            declared = manifest.get("hooks")
            for d in declared if isinstance(declared, list) else [declared]:
                if isinstance(d, str):
                    found.append((pr / d, None))
                elif isinstance(d, dict):     # inline: the events themselves
                    found.append((pr / ".claude-plugin" / "plugin.json",
                                  d.get("hooks") if isinstance(d.get("hooks"), dict) else d))
            read = set()
            for f, inline in found:
                if inline is None:
                    if f in read or not f.is_file():
                        continue
                    read.add(f)
                    inline = (_read_json(f) or {}).get("hooks")
                if isinstance(inline, dict):
                    out.append((key, str(f), inline))
    return out


# the one breaker that closes the MCP door too
DONTASK_NOTE = "defaultMode is dontAsk — it denies AskUserQuestion and the MCP approver"


def ask_question_breakers(project_root, home=None, env=None):
    """Every setting known to make AskUserQuestion return without showing the
    question (anthropics/claude-code#13439, #29547): an allow rule or a
    skill's allowed-tools naming it, a PreToolUse/PermissionRequest hook whose
    matcher catches it — in settings or in an enabled plugin — or dontAsk
    mode. [(where, what)]."""
    home = pathlib.Path(home or pathlib.Path.home())
    found = []
    for path in _settings_files(project_root, home):
        s = _read_json(path)
        if not s:
            continue
        perms = s.get("permissions") or {}
        for rule in perms.get("allow") or []:
            if "AskUserQuestion" in str(rule):
                found.append((str(path), f"permissions.allow has {rule!r}"))
        if str(perms.get("defaultMode", "")) == "dontAsk":
            found.append((str(path), DONTASK_NOTE))
        for event in ("PreToolUse", "PermissionRequest"):
            for entry in (s.get("hooks") or {}).get(event) or []:
                m = entry.get("matcher") if isinstance(entry, dict) else None
                if _matcher_hits(m, "AskUserQuestion"):
                    cmds = " ".join(h.get("command", "") for h in entry.get("hooks", [])
                                    if isinstance(h, dict))
                    found.append((str(path), f"{event} hook with matcher "
                                             f"{m!r} catches AskUserQuestion "
                                             f"({cmds[:60]})"))
    skill_dirs = [pathlib.Path(project_root) / ".claude" / "skills",
                  home / ".claude" / "skills"]
    for d in skill_dirs:
        if not d.is_dir():
            continue
        for sk in sorted(d.glob("*/SKILL.md")):
            try:
                tools = _frontmatter_value(sk.read_text(encoding="utf-8"),
                                           "allowed-tools")
            except OSError:
                continue
            if tools and "AskUserQuestion" in tools:
                found.append((str(sk), "allowed-tools pre-allows AskUserQuestion"))
    for plugin, where, hooks in enabled_plugin_hooks(project_root, home, env):
        for event in ("PreToolUse", "PermissionRequest"):
            for entry in hooks.get(event) or []:
                m = entry.get("matcher") if isinstance(entry, dict) else None
                if _matcher_hits(m, "AskUserQuestion"):
                    found.append((where, f"plugin {plugin}: {event} hook "
                                           + ("with no matcher" if m in (None, "")
                                              else f"with matcher {m!r}")))
                    break
    return found


# what each pythia hook does, the two gates first — a launcher that cannot
# start leaves exactly these off
HOOK_JOBS = (("hook pre-tool", "the sqlplus guard"),
             ("approve --hook", "the AskUserQuestion approval"),
             ("hook session-start", "the session guide"),
             ("hook post-tool", "the verdict reminders"))


def _and_list(words):
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def launch_checks(root, home, settings, local_settings):
    """One row per launcher the harness files start pythia with: can it? A
    Claude hook that cannot start fails open, so the row names exactly which
    jobs (HOOK_JOBS) no other launcher that starts on this machine still
    does: a gate among them (the guard, the approval hook) is a fail; only
    the session guide or the reminders, a warning; nothing at all — the
    team's copy erroring here while this machine's own launcher serves the
    same hooks — a warning that leaves keep-or-remove to the developer.
    Codex's hook carries only the session guide: a warning either way."""
    tracked = bool(local_settings.get("hooks")) and git_tracks(
        root, ".claude/settings.local.json")
    # role: shared (the team's file), own (this machine's), user (~/.claude,
    # every project of this user), codex (shared, the session guide only)
    sources = [(".claude/settings.json", settings.get("hooks"), "shared"),
               (".claude/settings.local.json", local_settings.get("hooks"),
                "shared" if tracked else "own"),
               ("~/.claude/settings.json",
                (_read_json(home / ".claude" / "settings.json") or {}).get("hooks"),
                "user"),
               (".codex/hooks.json",
                (_read_json(root / ".codex" / "hooks.json") or {}).get("hooks"),
                "codex")]
    found = []
    for where, hooks, role in sources:
        marks = {}
        for ev in (hooks or {}).values() if isinstance(hooks, dict) else []:
            for e in ev if isinstance(ev, list) else []:
                for h in (e.get("hooks") or []) if isinstance(e, dict) else []:
                    cmd = str(h.get("command", "")) if isinstance(h, dict) else ""
                    mark = _hook_mark(cmd)
                    if mark:
                        marks.setdefault(cmd[:cmd.index(mark)].strip(), set()).add(
                            LEGACY_HOOK_COMMANDS.get(mark, mark))
        for launcher, served in marks.items():
            found.append((where, role, launcher, served) + launcher_verdict(
                launcher, shared=role in ("shared", "codex")))
    out = []
    for i, (where, role, launcher, served, st, code, detail) in enumerate(found):
        broken = None
        if code in ("not-on-path", "cannot-start") and role == "codex":
            st = "warn"
            broken = ("Codex then misses only its session guide and live state; "
                      "AGENTS.md still carries the rules. The .codex/ files are "
                      f"shared and stay portable — `{invocation()} install` says "
                      "what this machine's ~/.codex/config.toml needs")
        elif code in ("not-on-path", "cannot-start"):
            # what the other Claude launchers that do start already serve
            others = [(w, s) for j, (w, r, _, s, _, c, _) in enumerate(found)
                      if j != i and r != "codex" and c == "ok"]
            covered = set().union(*[s for _, s in others]) if others else set()
            off = [label for mark, label in HOOK_JOBS if mark in served - covered]
            here = {"shared": ".claude/settings.json",
                    "own": ".claude/settings.local.json",
                    "user": "~/.claude/settings.json"}[role]
            if not off:
                st = "warn"
                names = list(dict.fromkeys(w for w, s in others if s & served))
                keep = ("Teammates may rely on them — keep them, or remove them "
                        "from .claude/settings.json" if role == "shared" else
                        f"Remove them from {here}")
                broken = (f"these hooks show an error on every call they match, "
                          f"while {' and '.join(names)} "
                          f"{'serve' if len(names) > 1 else 'serves'} the same "
                          f"hooks with a launcher that starts. {keep}")
            else:
                gate = any(m in served - covered for m, _ in HOOK_JOBS[:2])
                st = "fail" if gate else "warn"
                fix = {"shared": f"re-run `{invocation()} install` — it adds this "
                                 "machine's own launcher to "
                                 ".claude/settings.local.json and leaves the "
                                 "shared file to the team",
                       "own": f"re-run `{invocation()} install`, which picks an "
                              "interpreter that can",
                       "user": f"re-run `{invocation()} install -g`"}[role]
                broken = (f"{_and_list(off)} {'is' if len(off) == 1 else 'are'} "
                          f"off {'in every project' if role == 'user' else 'on this machine'}"
                          f"; {fix}")
        out.append((st, "hook launch",
                    f"{where}: {launcher_message(launcher, code, detail, broken)}"))
    return out


def harness_checks(project_root, home=None):
    """[(status, area, message)] — status ok | warn | fail | info."""
    import importlib.util
    root = pathlib.Path(project_root)
    out = []
    out.append(("ok" if sys.version_info >= (3, 9) else "fail", "python",
                f"{sys.version.split()[0]} at {sys.executable}"))
    has_driver = importlib.util.find_spec("oracledb") is not None
    out.append(("ok" if has_driver else "warn", "driver",
                "python-oracledb installed" if has_driver else
                "python-oracledb missing — pip install oracledb"))
    cfg = root / CONFIG_DIR / CONFIG_NAME
    if cfg.is_file():
        try:
            rows = connection_summary(json.loads(cfg.read_text(encoding="utf-8")))
            filled = [r for r in rows if r["user"]]
            out.append(("ok" if filled else "warn", "connections",
                        f"{len(rows)} configured, {len(filled)} with a user"
                        + ("" if filled else f" — fill in {cfg}")))
        except ValueError:
            out.append(("fail", "connections", f"{cfg} is not valid JSON"))
        out.append(credentials_check(root))
    else:
        out.append(("warn", "connections", f"no {CONFIG_DIR}/{CONFIG_NAME} — "
                                           f"{invocation()} install"))
    s = _read_json(root / ".claude" / "settings.json") or {}
    sl = _read_json(root / ".claude" / "settings.local.json") or {}
    cmds = json.dumps(s.get("hooks") or {}) + json.dumps(sl.get("hooks") or {})
    for mark, what in (("hook session-start", "SessionStart guide + state"),
                       ("hook pre-tool", "PreToolUse guard (reads, approved writes, bypass block)"),
                       ("approve --hook", "PostToolUse AskUserQuestion approval"),
                       ("hook post-tool", "PostToolUse verdict reminders")):
        legacy = mark == "hook session-start" and "guide --brief" in cmds
        out.append(("ok" if mark in cmds else "warn" if legacy else "warn",
                    "claude hooks",
                    f"{what}: " + ("wired" if mark in cmds else
                                   "legacy guide hook — re-run install" if legacy
                                   else f"missing — {invocation()} install")))
    home_dir = pathlib.Path(home or pathlib.Path.home())
    out.extend(launch_checks(root, home_dir, s, sl))
    mcp = _read_json(root / ".mcp.json") or {}
    local = local_mcp_entry(root, home_dir)
    if local is not None:
        st, msg = launcher_health(
            mcp_launcher(local) or str(local.get("command")),
            broken="the pythia_approve door stays closed (AskUserQuestion and "
                   f"the console remain); re-run `{invocation()} install`, which "
                   "registers it again with a launcher that starts")
        out.append((st, "approval door", "pythia_approve MCP tool registered in "
                    f"Claude Code's local scope (overrides .mcp.json) — {msg}"))
    elif "pythia" in (mcp.get("mcpServers") or {}):
        trusted = ("pythia" in (s.get("enabledMcpjsonServers") or [])
                   or s.get("enableAllProjectMcpServers"))
        entry = mcp["mcpServers"]["pythia"]
        st, msg = launcher_health(mcp_launcher(entry) or str(
            (entry or {}).get("command") if isinstance(entry, dict) else entry),
            shared=True,
            broken="the pythia_approve door stays closed (AskUserQuestion and "
                   f"the console remain); re-run `{invocation()} install` — it "
                   "registers this machine's launcher in Claude Code's local "
                   "scope, which overrides .mcp.json")
        out.append((st, "approval door", "pythia_approve MCP tool registered in "
                    ".mcp.json" + ("" if trusted else
                                   " (approve the server once in /mcp)")
                    + ("" if st == "ok" else f", but {msg}")))
    else:
        out.append(("warn", "approval door", "pythia MCP approver not in .mcp.json "
                    "— the AskUserQuestion card and the console remain; "
                    f"{invocation()} install adds it"))
    # Current Claude Code requires the dialog for AskUserQuestion, so a hook
    # or an allow rule naming it no longer answers it; dontAsk still denies
    # it, and the MCP prompt with it.
    for where, what in ask_question_breakers(root, home):
        if what == DONTASK_NOTE:
            out.append(("fail", "AskUserQuestion",
                        f"{what} — {where}. Both chat doors are closed; "
                        f"approvals need `{invocation()} approve <token>` at "
                        "the console. Leave dontAsk for any other mode"))
        else:
            out.append(("warn", "AskUserQuestion",
                        f"{what} — {where}. On older Claude Code this answered "
                        "AskUserQuestion before the developer saw it; current "
                        "versions require the dialog anyway. If a chat approval "
                        "ever comes back empty, pythia's hook says so and sends "
                        "the agent to the next door — remove this to rule it out"))
    cmd_md = root / "CLAUDE.md"
    has_block = cmd_md.is_file() and AGENTS_BEGIN in cmd_md.read_text(
        encoding="utf-8", errors="replace")
    out.append(("ok" if has_block else "warn", "claude.md",
                "CLAUDE.md carries the pythia block" if has_block else
                f"no pythia block in CLAUDE.md — the rules then reach Claude "
                f"only through the hooks; {invocation()} install adds it"))
    agents = root / "AGENTS.md"
    out.append(("ok" if agents.is_file() and AGENTS_BEGIN in agents.read_text(
        encoding="utf-8") else "info", "codex",
        "AGENTS.md carries the pythia block" if agents.is_file() and AGENTS_BEGIN
        in agents.read_text(encoding="utf-8") else "no pythia block in AGENTS.md "
        "(only needed for Codex and AGENTS.md agents)"))
    homes = {"project .claude/skills": root / ".claude" / "skills",
             "global ~/.claude/skills": pathlib.Path(home or pathlib.Path.home())
             / ".claude" / "skills"}
    pack = set(pack_names())
    present = {k: {p.name for p in v.glob("*") if (p / "SKILL.md").is_file()} & pack
               for k, v in homes.items() if v.is_dir()}
    present = {k: v for k, v in present.items() if v}   # no pythia skills: not ours
    for k, names in present.items():
        missing = sorted(pack - names)
        out.append(("ok" if not missing else "warn", "skills",
                    f"{k}: {len(names)}/{len(pack)} pythia skills"
                    + (f" — missing {', '.join(missing[:4])}" if missing else "")))
    if len([k for k, v in present.items() if v]) > 1:
        out.append(("warn", "skills", "pythia skills installed both globally and "
                    "per project — every menu entry shows twice"))
    if not present:
        out.append(("info", "skills", "no pythia skills under .claude/skills — "
                    "fine if installed as a plugin"))
    eff = load_policy(root)
    out.append(("info", "policy", ", ".join(f"{g}={v}" for g, (v, _) in eff.items())))
    conv = load_conventions(root)
    out.append(("ok" if conv else "info", "conventions",
                ("conventions.json with " + str(len((conv or {}).get("naming") or {}))
                 + " naming pattern(s)" + (", lint rules" if (conv or {}).get("lint") else "")
                 if conv else "none — pythia-conventions adopts a house style")))
    out.append(("info", "autopilot", lease_line(read_lease(root))))
    return out


def db_checks(conn, schema):
    out = []
    for q, fmt in (("db-version.sql", lambda rows: "; ".join(
            f"{r[0]} {r[1]}" for r in rows[:2])),
                   ("db-nls.sql", lambda rows: ", ".join(
                       f"{r[0]}={r[1]}" for r in rows))):
        try:
            _, rows = run_query(conn, load_query(q), {})
            out.append(("ok", "database", fmt(rows)))
            if q == "db-nls.sql":
                cs = {r[0]: r[1] for r in rows}.get("NLS_CHARACTERSET")
                if cs and cs not in ("AL32UTF8", "UTF8"):
                    out.append(("warn", "charset", f"{cs} cannot hold every "
                                "Vietnamese character — literals must go "
                                "through `pythia unistr`, NVARCHAR2 for data"))
        except Exception as e:            # noqa: BLE001
            out.append(("warn", "database", f"{q}: {e}"))
    try:
        _, rows = run_query(conn, load_query("schema-settings.sql"), {"s": schema})
        r = rows[0]
        units, scope, warn, invalid, ut = (int(x or 0) for x in r)
        out.append(("ok" if not units or scope >= units / 2 else "info",
                    "plscope", f"{scope}/{units} PL/SQL units compiled with "
                    "PL/Scope identifiers (pythia apply turns it on per write)"))
        out.append(("ok" if invalid == 0 else "warn", "invalid",
                    f"{invalid} INVALID object(s) in {schema}"
                    + (f" — {invocation()} invalid" if invalid else "")))
        out.append(("ok" if ut else "info", "utplsql",
                    "utPLSQL installed" if ut else "utPLSQL not visible — "
                    "unit tests need it (pythia-setup reference/environment.md)"))
    except Exception as e:                # noqa: BLE001
        out.append(("warn", "schema", f"schema-settings.sql: {e}"))
    return out


def cmd_doctor(conn, schema, ns):
    """Is the harness wired, and what in this machine's setup will break it.
    --db adds the database environment."""
    rows = harness_checks(ns.project_root)
    if conn is not None:
        rows += db_checks(conn, schema)
        warn = privilege_warning(conn, schema, getattr(ns, "conn_user", ""))
        if warn:
            rows.append(("warn", "privileges", warn.splitlines()[0]))
    if ns.json:
        print(json.dumps([{"status": s, "area": a, "message": m}
                          for s, a, m in rows]))
        return
    en = getattr(ns, "color", False)
    color = {"ok": "green", "warn": "yellow", "fail": "red", "info": "dim"}
    for s, a, m in rows:
        print(f"  {paint(f'{s:<4}', color[s], en)} {a:<15} {m}")
    bad = sum(1 for s, _, _ in rows if s == "fail")
    if bad:
        print(paint(f"\n  {bad} problem(s) to fix first — each fail line "
                    "names its fix.", "red", en))
    if conn is None:
        print(f"\n  Database environment: {invocation()} doctor --db")


# --- commands: skill — help the team grow its own pack -------------------------

SKILL_TEMPLATE = """---
name: {name}
description: Use when {trigger}
---

# {title}

**Announce at start:** "Using {name} — <one line on what you will do first>."

**Phase:** Learn → Ask → Do — <which movement this skill owns, and why>

<One paragraph: the situation this skill exists for, and the cost of getting
it wrong. A rule with a named cost gets followed.>

## The Iron Law

```
<ONE SENTENCE THE AGENT MUST NOT BREAK>
```

## The Workflow

1. **Learn.** <the pythia commands that establish the facts — never memory>
2. **Ask.** <the decisions that belong to the developer, with options>
3. **Do.** <the steps; writes go through `pythia apply` / `pythia deploy`>
4. **Verify.** <the evidence that proves it worked — pythia-verify>

## Red Flags — STOP if you catch yourself thinking

| Thought | Reality |
|---------|---------|
| "<the rationalisation>" | <why it is wrong here> |

## When NOT to use this skill

- <the neighbouring situation, and the skill that owns it>
"""


def skill_lint_findings(path):
    """Problems that stop a SKILL.md from triggering or loading — the same
    contract tests/test_phase5.py holds the shipped pack to."""
    path = pathlib.Path(path)
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"cannot read: {e}"]
    m = re.match(r"^---\n(.*?)\n---\n", text.replace("\r\n", "\n"), re.S)
    if not m:
        return ["no YAML frontmatter (--- name/description --- at the top)"]
    fields = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][\w.-]*):\s+(.*)$", line)
        if km:
            fields[km.group(1)] = km.group(2)
            if km.group(2)[:1] not in "\"'" and ": " in km.group(2):
                out.append(f"frontmatter {km.group(1)!r} holds an unquoted "
                           "': ' — invalid YAML, the skill loses its trigger")
    folder = path.parent.name
    if fields.get("name") != folder:
        out.append(f"name {fields.get('name')!r} must match the folder {folder!r}")
    desc = fields.get("description", "")
    if not desc:
        out.append("description missing — the agent cannot know when to fire")
    elif not desc.lower().startswith(("use when", "use before")):
        out.append("description must open with its trigger: 'Use when ...' "
                   "or 'Use before ...'")
    if len(desc) > 1024:
        out.append(f"description is {len(desc)} chars (max 1024)")
    n = text.count("\n") + 1
    if n > 150:
        out.append(f"{n} lines (max 150) — push detail into reference/")
    if "**Phase:**" not in text:
        out.append("no **Phase:** line — say which of Learn / Ask / Do it owns")
    for ref in re.findall(r"`?(reference/[\w./-]+\.md)`?", text):
        if not (path.parent / ref).is_file():
            out.append(f"links {ref}, which does not exist")
    return out


def cmd_skill(conn, schema, ns):
    root = pathlib.Path(ns.project_root)
    if ns.action == "new":
        name = ns.name or ""
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", name):
            sys.exit("Skill names are lowercase words joined by '-', e.g. "
                     "team-release-notes.")
        base = pathlib.Path(ns.dir) if ns.dir else root / ".claude" / "skills"
        path = base / name / "SKILL.md"
        if path.exists():
            sys.exit(f"{path} already exists — edit it, or pick another name.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SKILL_TEMPLATE.format(
            name=name, title=name.replace("-", " ").capitalize(),
            trigger=" ".join(ns.trigger or []) or "<the situation, in the "
                    "developer's words — the agent fires on these>"),
            encoding="utf-8")
        print(f"Wrote {path}\nFill it in (pythia-skill-author interviews the "
              f"developer for the content), then: {invocation()} skill lint "
              f"{path.parent}")
        return
    given = ([ns.name] if ns.name else []) + list(ns.paths or [])
    targets = [pathlib.Path(p) for p in given] or [
        root / ".claude" / "skills", root / ".agents" / "skills"]
    files = []
    for t in targets:
        if t.is_file():
            files.append(t)
        elif (t / "SKILL.md").is_file():
            files.append(t / "SKILL.md")
        elif t.is_dir():
            files.extend(sorted(t.glob("*/SKILL.md")))
    if not files:
        sys.exit("No SKILL.md found. Pass a skill folder or file.")
    report = {str(f): skill_lint_findings(f) for f in files}
    if ns.json:
        print(json.dumps(report))
    else:
        for f, probs in report.items():
            print(f"{'ok  ' if not probs else 'FAIL'} {f}")
            for p in probs:
                print(f"       {p}")
    if any(report.values()):
        sys.exit(2)


COMMANDS = {"check": cmd_check, "ls": cmd_ls, "src": cmd_src, "args": cmd_args,
            "ddl": cmd_ddl, "cols": cmd_cols, "grep": cmd_grep, "sql": cmd_sql,
            "invalid": cmd_invalid, "errors": cmd_errors, "deps": cmd_deps,
            "impact": cmd_impact, "similar": cmd_similar, "plscope": cmd_plscope,
            "policy": cmd_policy, "journal": cmd_journal, "apply": cmd_apply,
            "approve": cmd_approve,
            "conventions": cmd_conventions, "guide": cmd_guide, "connections": cmd_connections, "install": cmd_install,
            "unistr": cmd_unistr, "agent-user": cmd_agent_user,
            "history": cmd_history, "mcp": cmd_mcp,
            "autopilot": cmd_autopilot, "deploy": cmd_deploy, "lint": cmd_lint,
            "explain": cmd_explain, "stats": cmd_stats,
            "comments": cmd_comments, "hook": cmd_hook, "doctor": cmd_doctor,
            "skill": cmd_skill}

NO_DB_COMMANDS = {"policy", "journal", "install", "unistr", "guide", "connections",
                  "history", "approve", "mcp", "autopilot", "lint", "hook",
                  "doctor", "skill"}


# --- CLI ---------------------------------------------------------------------

def build_parser():
    def common():
        """A fresh set of shared options per subcommand. argparse's `parents`
        shares the very same action objects, so one subcommand's set_defaults
        would otherwise rewrite every other subcommand's default."""
        c = argparse.ArgumentParser(add_help=False)
        c.add_argument("--conn", help="connection name from connections.json")
        c.add_argument("--json", action="store_true", help="machine-readable output")
        c.add_argument("--limit", type=int, default=200,
                       help="max rows for list output, 0 = no cap (default 200)")
        c.add_argument("--max-lines", type=int, default=2000, dest="max_lines",
                       help="max source/DDL lines, 0 = no cap (default 2000)")
        c.add_argument("--offset", type=int, default=0,
                       help="skip N lines/rows first (continue truncated output)")
        c.add_argument("--raw", action="store_true",
                       help="plain text, no line numbers or unit headers")
        return c

    p = argparse.ArgumentParser(
        prog="pythia", description=__doc__, allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    # no prefix matching: `--ho` must never reach --hook, nor `--ye` --yes —
    # a hook that reads the full spelling would otherwise miss the flag
    _add = sub.add_parser
    sub.add_parser = lambda *a, **kw: _add(*a, allow_abbrev=False, **kw)
    sub.add_parser("check", parents=[common()],
                   help="connectivity + object counts for the schema")
    s = sub.add_parser("ls", parents=[common()], help="find objects by name pattern")
    s.add_argument("pattern", help="LIKE pattern, e.g. \"PKG_%%\"")
    s = sub.add_parser("src", parents=[common()],
                       help="PL/SQL source with Oracle line numbers")
    s.add_argument("name")
    s.add_argument("--body", action="store_true", help="only *BODY units")
    s.add_argument("--spec", action="store_true", help="only spec units")
    s = sub.add_parser("args", parents=[common()], help="procedure/function signature")
    s.add_argument("name")
    s = sub.add_parser("ddl", parents=[common()], help="DDL via DBMS_METADATA")
    s.add_argument("type", help="e.g. TABLE, INDEX, VIEW, PACKAGE_BODY")
    s.add_argument("name")
    s = sub.add_parser("cols", parents=[common()], help="columns and data types")
    s.add_argument("name")
    s = sub.add_parser("grep", parents=[common()], help="search all PL/SQL source")
    s.add_argument("pattern")
    s = sub.add_parser("sql", parents=[common()], help="free query (SELECT/WITH only)")
    s.add_argument("statement", nargs="+")
    sub.add_parser("invalid", parents=[common()],
                   help="every INVALID object in the schema")
    s = sub.add_parser("errors", parents=[common()],
                       help="compilation errors with line and column")
    s.add_argument("name", nargs="?", default=None,
                   help="object name; omit for every object in the schema")
    s = sub.add_parser("deps", parents=[common()],
                       help="what an object depends on")
    s.add_argument("name")
    s.add_argument("--depth", type=int, default=3,
                   help="levels to walk (default 3)")
    s.add_argument("--with-sys", action="store_true", dest="with_sys",
                   help="include SYS/PUBLIC built-ins, hidden by default")
    s = sub.add_parser("impact", parents=[common()],
                       help="what depends on an object — run this before changing it")
    s.add_argument("name")
    s.add_argument("--depth", type=int, default=3,
                   help="levels to walk (default 3)")
    s = sub.add_parser("similar", parents=[common()],
                       help="programs named like this one — copy their conventions")
    s.add_argument("name")
    s.set_defaults(limit=20)
    s = sub.add_parser("plscope", parents=[common()],
                       help="exact identifier usages from PL/Scope")
    s.add_argument("name")
    s = sub.add_parser("policy", parents=[common()],
                       help="show or change the write policy")
    s.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    s.add_argument("group", nargs="?", choices=sorted(POLICY_DEFAULTS))
    s.add_argument("value", nargs="?", choices=["allow", "confirm", "deny"])
    s = sub.add_parser("journal", parents=[common()],
                       help="list, inspect, export and restore write snapshots")
    s.add_argument("action", nargs="?",
                   choices=["list", "show", "diff", "export", "prune", "restore"],
                   default="list")
    s.add_argument("id", nargs="?")
    s.add_argument("--what", choices=["after", "before", "restore"],
                   default="after", help="which file export writes (default after)")
    s.add_argument("--confirm", metavar="TOKEN")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--depth", type=int, default=3)
    s = sub.add_parser("apply", parents=[common()],
                       help="preview and apply one statement with snapshot and verify")
    s.add_argument("file", help="file containing exactly one statement")
    s.add_argument("--confirm", metavar="TOKEN",
                   help="token printed by the preview; applies only if nothing changed since")
    s.add_argument("--yes", action="store_true",
                   help="apply without stopping; the full preview still prints and journals")
    s.add_argument("--depth", type=int, default=3,
                   help="impact depth for the preview (default 3)")
    s.add_argument("--preview", action="store_true",
                   help="preview only, even under an active autopilot lease")
    s = sub.add_parser("deploy", parents=[common()],
                       help="run a release script (or several, or a directory) "
                            "statement by statement through apply's six steps")
    s.add_argument("paths", nargs="+", help="files and/or directories of .sql")
    s.add_argument("--confirm", metavar="TOKEN",
                   help="the deploy token the developer approved")
    s.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--depth", type=int, default=3)
    s.add_argument("--preview", action="store_true",
                   help="preview only, even under an active autopilot lease")
    s.add_argument("--from", type=int, default=1, dest="from_n", metavar="N",
                   help="start at statement N (resume a stopped deploy)")
    s = sub.add_parser("autopilot", parents=[common()],
                       help="delegate a session: one approval lets apply land "
                            "covered statements until it ends")
    s.add_argument("action", nargs="?",
                   choices=["status", "request", "on", "off", "report"],
                   default="status")
    s.add_argument("--groups", help="comma list: plsql_source, comments, "
                                    "structural, data_dml, grants, or all "
                                    "(default plsql_source,comments)")
    s.add_argument("--hours", type=float, default=None,
                   help=f"lease length (default {LEASE_HOURS_DEFAULT}, "
                        f"max {LEASE_HOURS_MAX})")
    s.add_argument("--max-writes", type=int, default=None, dest="max_writes",
                   help=f"write budget (default {LEASE_WRITES_DEFAULT})")
    s.add_argument("--destructive", action="store_true",
                   help="also cover DROP/TRUNCATE/ALTER ... DROP/REVOKE/"
                        "UPDATE or DELETE without WHERE (none can be undone)")
    s.add_argument("--note", nargs="*", help="the task, shown on the card")
    s.add_argument("--token", help="report: a past lease's token")
    s = sub.add_parser("lint", parents=[common()],
                       help="check .sql files against the built-in standards "
                            "and the LEAD's rules (conventions.json); exit 2 "
                            "on errors")
    s.add_argument("paths", nargs="+")
    s = sub.add_parser("explain", parents=[common()],
                       help="optimizer plan for a SELECT (EXPLAIN PLAN + "
                            "DBMS_XPLAN; parsed, never run)")
    s.add_argument("statement", nargs="*")
    s.add_argument("--file", help="read the query from a file")
    s.add_argument("--format", default="TYPICAL",
                   choices=["BASIC", "TYPICAL", "ALL"])
    s = sub.add_parser("stats", parents=[common()],
                       help="optimizer statistics, indexes and unindexed "
                            "foreign keys of a table")
    s.add_argument("name")
    s = sub.add_parser("comments", parents=[common()],
                       help="table and column comments, and which are missing")
    s.add_argument("name")
    s.add_argument("--scaffold", action="store_true",
                   help="print COMMENT ON statements for the missing ones")
    s = sub.add_parser("doctor", parents=[common()],
                       help="is the harness wired; what will break approvals; "
                            "--db adds the database environment")
    s.add_argument("--db", action="store_true",
                   help="also check the database (version, charset, PL/Scope, "
                        "INVALID objects, utPLSQL)")
    s = sub.add_parser("hook", parents=[common()],
                       help="client hook entry point (Claude Code runs this; "
                            "payload on stdin)")
    s.add_argument("event", choices=["session-start", "pre-tool", "post-tool"])
    s = sub.add_parser("skill", parents=[common()],
                       help="scaffold or lint the team's own skills")
    s.add_argument("action", choices=["new", "lint"])
    s.add_argument("name", nargs="?", help="new: the skill name")
    s.add_argument("paths", nargs="*", help="lint: skill folders or files")
    s.add_argument("--dir", help="new: where to create it "
                                 "(default .claude/skills)")
    s.add_argument("--trigger", nargs="*", help="new: the 'Use when' text")
    s = sub.add_parser("approve", parents=[common()],
                       help="the developer approves a preview: mints the "
                            "one-time grant apply --confirm requires")
    s.add_argument("token", nargs="*", help="token(s) printed by the preview")
    s.add_argument("--card", action="store_true",
                   help="print the approval card the agent asks with in chat "
                        "(AskUserQuestion); mints nothing, needs no console")
    s.add_argument("--hook", action="store_true",
                   help="Claude Code PostToolUse hook on AskUserQuestion: "
                        "reads the payload on stdin and mints the grant the "
                        "developer answered Approve to")
    sub.add_parser("connections", parents=[common()],
                   help="list configured connections — names, users, "
                        "targets; never passwords")
    s = sub.add_parser("guide", parents=[common()],
                   help="the operating model: Learn, Ask, Do — the whole "
                        "harness on one page, no database needed")
    s.add_argument("--brief", action="store_true",
                   help="the one-page version, sized for a session preamble")
    s = sub.add_parser("conventions", parents=[common()],
                   help="show the project's house-style naming patterns")
    s.add_argument("--init", action="store_true",
                   help="write starter conventions.json and conventions.md "
                        "into .pythia/ (never overwrites)")
    s.add_argument("--scan", action="store_true",
                   help="read naming patterns off the live schema and "
                        "propose them (needs a connection)")
    s.add_argument("--check", action="store_true",
                   help="measure the configured patterns against the "
                        "schema: coverage and the names that miss")
    s = sub.add_parser("agent-user", parents=[common()],
                       help="SQL for a least-privilege proxy agent user; "
                            "--save adds it to connections.json")
    s.add_argument("--name", help="agent user name (default <OWNER>_AGENT)")
    s.add_argument("--save", action="store_true",
                   help="add the new credential as <conn>_agent and make it "
                        "the default connection")
    s = sub.add_parser("history", parents=[common()],
                       help="every captured version of one object, newest "
                            "first — pick one to restore")
    s.add_argument("name")
    s = sub.add_parser("unistr", parents=[common()],
                       help="Oracle unistr('...') literal for non-ASCII text "
                            "(Vietnamese messages stay exact)")
    s.add_argument("text", nargs="*", help="text; omit to read stdin")
    s.add_argument("--loi", action="store_true",
                   help="wrap as 'loi:'||unistr(...)||':loi'")
    sub.add_parser("mcp", parents=[common()],
                   help="run the MCP approver for Codex (stdio; Codex launches "
                        "it — the developer approves via an elicitation)")
    s = sub.add_parser("install", parents=[common()],
                       help="install the skill pack, scaffold .pythia/ config, wire the Claude Code hooks")
    s.add_argument("-g", "--global", dest="glob", action="store_true",
                   help="install the skill pack machine-wide (serves every "
                        "project; per-project installs then skip skills)")
    s.add_argument("--add-to-path", action="store_true", dest="add_to_path",
                   help="put the pythia command on your PATH without asking "
                        "(Windows; user PATH only)")
    s.add_argument("--no-path", action="store_true", dest="no_path",
                   help="leave your PATH alone (Windows: without a console to "
                        "ask, install otherwise adds pythia's directory to the "
                        "user PATH)")
    s.add_argument("--no-hooks", action="store_true", dest="no_hooks",
                   help="leave Claude Code's .claude/settings.json alone "
                        "(no approve-in-chat hook, no session-start guide)")
    s.add_argument("--source", default=DEFAULT_SKILLS_SOURCE,
                   help="skills repo for `npx skills add` "
                        f"(default {DEFAULT_SKILLS_SOURCE})")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    forbid_write_flag(argv)
    # stdin too, and before anything reads it: the AskUserQuestion hook
    # payload is UTF-8, and Windows would decode it as cp1252 — an em dash
    # arrives as mojibake and no approval card ever matches again.
    # line_buffering keeps stdout in order with stderr when both are pipes,
    # so a driver error lands after the preview it belongs to, not above it.
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", line_buffering=True)
        except (AttributeError, ValueError):
            pass
    ns = build_parser().parse_args(argv)
    # refusals that need no database come first — an unreachable or locked
    # connection must never mask them
    if ns.command == "sql" and not is_readonly_sql(
            " ".join(ns.statement).strip().rstrip(";")):
        sys.exit("Only SELECT/WITH statements are allowed; sql is read-only."
                 f"\nThe write path is `{invocation()} apply <file>` — "
                 "snapshot, preview and verify included.")
    if getattr(ns, "yes", False) and not human_at_the_keyboard():
        sys.exit(HEADLESS_YES_MSG)
    ns.color = color_enabled() and not ns.json
    if ns.color and os.name == "nt":
        # Constant empty string, never user input — safe from injection. This
        # no-op shell call is the stdlib idiom that flips on ANSI (VT) escape
        # processing in legacy Windows consoles; Windows Terminal needs nothing.
        os.system("")
    cwd = pathlib.Path.cwd()
    cfg, root = find_config(cwd, os.environ)
    ns.project_root = root if root is not None else cwd
    # conventions reads the schema only for --scan and --check; listing what
    # is configured, or writing the template, must work with no database at all
    offline_conventions = ns.command == "conventions" and not (
        getattr(ns, "scan", False) or getattr(ns, "check", False))
    if (ns.command in NO_DB_COMMANDS or offline_conventions) and not (
            ns.command == "journal" and getattr(ns, "action", "") == "restore") \
            and not (ns.command == "doctor" and getattr(ns, "db", False)):
        COMMANDS[ns.command](None, None, ns)
        return
    name, c = resolve_connection(cfg, ns.conn, os.environ, cwd, root)
    ns.conn_name = name
    ns.conn_user = c.get("user", "")
    ns.schema = (c.get("schema") or c["user"]).upper()
    print(f"-- connection={name} schema={ns.schema}", file=sys.stderr)
    try:
        import oracledb
    except ModuleNotFoundError:
        sys.exit("The 'oracledb' package is required to connect: pip install oracledb")
    try:
        pool = open_pool(c)
        conn = acquire(pool, session_should_be_readonly(
            ns.command, getattr(ns, "action", "") or ""))
    except (oracledb.Error, OSError) as e:
        # OSError too: a DNS or socket failure arrives raw from the socket layer,
        # not wrapped as an oracledb.Error.
        sys.exit(connect_failure_message(e, name, c.get("user")))
    try:
        if ns.command == "journal":           # only restore reaches here
            if not ns.id:
                sys.exit(f"Usage: {invocation()} journal restore <id>")
            ns.file = f"journal:{ns.id}"
            code = run_restore(conn, ns.schema, ns)
            if code:
                sys.exit(code)
        else:
            COMMANDS[ns.command](conn, ns.schema, ns)
    except oracledb.Error as e:
        sys.exit(f"Oracle error: {e}")
    finally:
        pool.close(force=True)


if __name__ == "__main__":
    main()
