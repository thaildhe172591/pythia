#!/usr/bin/env python3
"""pythia — ask an Oracle Database directly instead of reading stale dumps.

Reads are plain commands; sql accepts SELECT/WITH only. Writes go through
`apply` — snapshot, impact, preview, apply, verify, report — gated by
.pythia/policy.json. There is no --write flag: the write path is `apply`,
nothing else.

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
  pythia apply PKG_ORDER_BODY.sql
  pythia apply PKG_ORDER_BODY.sql --confirm 7f3a91
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
    "name-occupants.sql": {"s", "n"},
    "object-names.sql": {"s"},
}


# --- pure helpers (covered by tests/test_phase1.py) --------------------------

def is_readonly_sql(stmt):
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


def offer_path_fix(scripts_dir, interactive, forced=False):
    """Put pythia on PATH for the developer instead of handing them a command
    to run. Only ever touches the user PATH, and only with a yes."""
    if not scripts_dir or os.name != "nt":
        return False
    if not forced:
        if not interactive:
            return False
        try:
            answer = input(NL + "Add it to your PATH now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer not in ("", "y", "yes"):
            return False
    try:
        changed = add_to_user_path(scripts_dir)
    except OSError as e:
        print(f"Could not update your PATH: {e}")
        return False
    print(NL + f"  {'Added' if changed else 'Already in'} your user PATH: {scripts_dir}")
    warn = path_length_warning(os.environ.get("PATH", ""))
    if warn:
        print("  " + warn)
    print("  Open a NEW terminal for it to take effect — an already-running")
    print("  one keeps the environment it started with, and so do its tabs.")
    print(f"  Until then: {pathlib.Path(sys.executable).stem} -m pythia <command>")
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
            + pathlib.Path(sys.executable).stem + " -m pythia <command>",
        ]))
    out = ["`pythia` is installed but not on your PATH."]
    if scripts_dir:
        out.append("  It lives in: " + str(scripts_dir))
        if os.name == "nt":
            # Deliberately not a PowerShell one-liner over $env:PATH:
            # that variable is system and user merged, so writing it
            # back into user scope copies every system entry into the
            # user's. Let the tool do it correctly instead.
            out.append("  Put it there with: "
                       + pathlib.Path(sys.executable).stem
                       + " -m pythia install --add-to-path")
        else:
            out.append("  Add it to your shell profile:")
            out.append("    export PATH=\"$PATH:" + str(scripts_dir) + "\"")
    else:
        out.append("  Add pip's scripts directory to PATH.")
    out.append("Either way, this works right now, PATH or no PATH:")
    out.append("  " + pathlib.Path(sys.executable).stem + " -m pythia <command>")
    return "\n".join(out)


def invocation():
    """How to invoke this tool, exactly as the user actually ran it. Every
    printed command must be paste-able: a bare `pythia ...` is
    CommandNotFound for anyone running from source."""
    main_spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if getattr(main_spec, "name", "") == "pythia":
        # `python -m pythia` — argv[0] is the module's site-packages path,
        # which nobody typed; the -m form is the paste-able one
        return f"{pathlib.Path(sys.executable).stem} -m pythia"
    prog = sys.argv[0] or "pythia"
    if prog.lower().endswith(".py"):
        # the interpreter actually running us: "python3" on most Linux/macOS,
        # "python" on Windows — a hardcoded "python" would not paste on Ubuntu
        return f"{pathlib.Path(sys.executable).stem} {prog}"
    return pathlib.Path(prog).name


HEADLESS_YES_MSG = (
    "--yes is the developer's flag, and no terminal is attached to this "
    "session.\nAgents preview, relay the diff and the impact verbatim, "
    "STOP, and pass --confirm <token> only after the developer's explicit "
    "approval in chat.\n(Real pipelines set PYTHIA_CI=1.)")


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


def _load_config(path):
    if os.name == "posix":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
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

GROUPS = ("plsql_source", "data_dml", "structural", "grants", "session")

PLSQL_SOURCE_RE = re.compile(
    r"^create\s+(?:or\s+replace\s+)?(?:(?:no)?editionable\s+|(?:no)?force\s+)*"
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
    if PLSQL_SOURCE_RE.match(s):
        return "plsql_source"
    if re.match(r"^(insert|update|delete|merge)\b", s, re.I):
        return "data_dml"
    if re.match(r"^(grant|revoke)\b", s, re.I):
        return "grants"
    if re.match(r"^(alter|drop|truncate|rename|create)\b", s, re.I):
        return "structural"
    if re.match(r"^(begin|declare)\b", s, re.I):
        return "anonymous"
    return None


def parse_object(sql):
    """(type, name, schema|None) from a plsql_source statement. The parsed
    identity drives the snapshot — getting it wrong would snapshot the wrong
    object and silently destroy the only undo, hence the strict match."""
    s = skip_leading_noise(sql)
    m = PLSQL_SOURCE_RE.match(s)
    if not m:
        sys.exit("Cannot parse the object type from this CREATE statement.")
    otype = " ".join(m.group(1).upper().split())
    rest = s[m.end():]
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
        if ";" in body:
            sys.exit("The file contains more than one statement. pythia apply "
                     "takes exactly one statement per file — split it.")
        return body
    return text


POLICY_DEFAULTS = {"plsql_source": "confirm", "data_dml": "deny",
                   "structural": "deny", "grants": "deny", "session": "allow"}

ROLLBACK_TABLE = """\
Is rollback real?  (this table also appears in README.md and pythia-apply)
  plsql_source  Yes - completely. Source is recoverable from ALL_SOURCE.
  data_dml      No. After commit only Flashback Query remains, within undo retention.
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


def render_restore(obj_type, name, before_text):
    """The statement that puts things back. For an object that did not exist,
    undo means DROP — a genuinely different promise than restoring source, so
    the caller records created=True and the report says it plainly."""
    if before_text.strip():
        return "CREATE OR REPLACE " + before_text.rstrip() + "\n"
    return f"DROP {obj_type} {name}\n"


def write_journal_entry(root, obj_type, name, before, after, meta, now=None):
    """Snapshot on disk before anything touches the database. Nothing can turn
    this off: DDL commits itself, so this directory is the only undo there is."""
    import datetime
    now = now or datetime.datetime.now()
    eid = (now.strftime("%Y-%m-%dT%H-%M-%S")
           + f"_{name}_{obj_type.replace(' ', '-')}")
    d = journal_root(root) / eid
    n = 1
    while d.exists():          # apply + restore can land in the same second;
        n += 1                 # overwriting the previous entry would destroy
        d = journal_root(root) / f"{eid}-{n}"   # the only undo there is
    eid = d.name
    d.mkdir(parents=True)
    (d / "before.sql").write_text(before, encoding="utf-8")
    (d / "after.sql").write_text(after, encoding="utf-8")
    (d / "restore.sql").write_text(render_restore(obj_type, name, before),
                                   encoding="utf-8")
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
    if command == "apply":
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
  }
}
"""

CONVENTIONS_PROSE_TEMPLATE = """# Project conventions

Rules for anyone — human or agent — writing PL/SQL in this schema. Agents are
told to read this before writing, and it outranks the generic patterns the
skill pack ships with.

`conventions.json` next to this file holds the naming patterns as regexes;
every `pythia apply` preview warns when a new object's name does not match.
Keep the two in step: this file explains, that file enforces.

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
    unknown = set(conv) - {"naming"}
    if unknown:
        sys.exit(f"Unknown key {sorted(unknown)[0]!r} in {path} — "
                 "supported: \"naming\".")
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
    if action == "deny":
        extra = ("no snapshot can undo it after commit"
                 if group in ("data_dml", "structural", "grants")
                 else "policy forbids it")
        sys.exit(f"Refused: {group} is set to deny — {extra}.\n"
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
        if blockers:
            sys.exit(f"{name} already exists as {', '.join(blockers)} in {schema} "
                     "— CREATE OR REPLACE cannot change an object's type "
                     "(ORA-00955 would follow).\n"
                     "Changing the type means DROP first — structural, and the "
                     "policy on that group applies:\n"
                     f"  {invocation()} policy set structural confirm   "
                     "(only if you accept losing the old object)")
    else:
        # confirm-mode DML/DDL/grants: no object identity, no snapshot — the
        # journal records the statement itself so at least *what ran* is kept.
        otype, name = group.upper(), "STATEMENT"

    # 1. SNAPSHOT — before anything else, unconditionally.
    db_source = ""
    if group == "plsql_source":
        _, rows = run_query(conn, load_query("object-source.sql"),
                            {"s": schema, "n": name, "t": otype})
        db_source = "".join(cell(r[0]) for r in rows)
    token = apply_token(otype, name, file_text, db_source)
    if ns.yes and not human_at_the_keyboard():
        sys.exit(HEADLESS_YES_MSG)
    confirmed = bool(ns.yes) or ns.confirm == token
    if ns.confirm and ns.confirm != token:
        sys.exit("The confirmation token does not match: the file or the "
                 "database object changed since that preview. Preview again:\n"
                 f"  {invocation()} apply {ns.file}")

    _, inv_rows = run_query(conn, load_query("invalid-objects.sql"), {"s": schema})
    invalid_before = [(r[0], r[1]) for r in inv_rows]
    meta = {"schema": schema, "connection": ns.conn_name, "group": group,
            "token": token, "applied": False,
            "confirmed_via": ("yes" if ns.yes else
                              "token" if confirmed else "preview"),
            "tty": human_at_the_keyboard(),
            "invalid_before": invalid_before, **(origin or {})}
    entry = write_journal_entry(ns.project_root, otype, name, db_source,
                                file_text, meta)
    created = not db_source.strip()

    # 2. IMPACT
    summary = ""
    if group == "plsql_source":
        _, dep_rows = run_query(conn, load_query("impact.sql"),
                                {"s": schema, "n": name, "depth": ns.depth})
        summary = impact_summary(dep_rows)

    # 3. PREVIEW — diff like against like: ALL_SOURCE never stores the
    # CREATE OR REPLACE header, so prepend it before comparing, or an
    # unchanged object would show a phantom two-line change forever.
    base = ("CREATE OR REPLACE " + db_source) if db_source.strip() else ""
    diff_text, changed = render_diff(base, stmt)
    warn = privilege_warning(conn, schema, ns.conn_user)
    style = (naming_violation(otype, name, load_conventions(ns.project_root))
             if group == "plsql_source" else None)
    if ns.json:
        print(json.dumps({"ok": True, "object": name, "type": otype,
                          "created": created, "changed_lines": changed,
                          "summary": summary, "warning": warn,
                          "naming_warning": style, "token": token,
                          "journal": entry, "will_apply": confirmed}))
    else:
        en = getattr(ns, "color", False)
        if created:
            head = "new object"
        elif changed == 0:
            head = "no source change (recompile)"
        else:
            head = f"{changed} lines changed"
        print(f"\n  {paint(f'{name} ({otype})', 'bold', en)} in {schema} — {head}")
        if summary:
            print(f"  {summary.lstrip('- ')}")
        if warn:
            print(f"\n  {paint(warn, 'yellow', en)}")
        if style:
            print(f"\n  {paint('! ' + style, 'yellow', en)}")
        if diff_text:
            print()
            for ln in diff_text.splitlines():
                print(f"  {paint_diff_line(ln, en)}")
        print(paint(f"\n  Snapshot saved: {journal_root(ns.project_root) / entry}",
                    "dim", en))
        if not confirmed:
            print(paint("  Rollback file for the current database version — "
                        "use it if this is\n  run by hand instead: "
                        f"{journal_root(ns.project_root) / entry / 'restore.sql'}",
                        "dim", en))
        if not confirmed:
            print(f"\n  To apply:\n    "
                  + paint(f"{invocation()} apply {ns.file} --confirm {token}",
                          "cyan", en))
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
        cur.execute(stmt)

    # 5. VERIFY
    err_rows = []
    invalid_after = invalid_before
    if group == "plsql_source":
        _, err_rows = run_query(conn, load_query("compile-errors.sql"),
                                {"s": schema, "n": name})
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
    if ns.json:
        print(json.dumps({"ok": ok, "applied": True, "object": name,
                          "type": otype, "errors": [list(r) for r in own_errors],
                          "newly_invalid": [list(x) for x in broke],
                          "restore": f"{invocation()} journal restore {entry}",
                          "restore_is_drop": created,
                          "restore_blocked_by_policy": (
                              created and undo_group_action(ns) == "deny"),
                          "exit": 0 if ok else 3}))
    else:
        en = getattr(ns, "color", False)
        if ok:
            print(paint(f"\n  Applied {name} ({otype}).", "green", en))
            print(paint("  Compiled clean. No new INVALID objects.", "green", en))
        else:
            if own_errors:
                print(paint(f"\n  Applied {name} ({otype}) — but it did not "
                            "compile cleanly:", "red", en))
                for r in own_errors:
                    print(paint(f"    {r[3]}:{r[4]} {r[5]} {str(r[6]).strip()}",
                                "red", en))
            else:
                print(paint(f"\n  Applied {name} ({otype}) — it compiled, but "
                            "broke other objects:", "red", en))
            if broke:
                print(f"  {len(broke)} objects were VALID before and are INVALID now:")
                for n2, t2 in broke:
                    print(paint(f"    {n2} ({t2})", "red", en))
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
            print(paint("  ...but that restore is a DROP, and structural is "
                        "set to deny, so it will be refused.", "yellow", en))
            print(paint("  The developer decides: "
                        f"{invocation()} policy set structural confirm",
                        "yellow", en))
    return 0 if ok else 3


def cmd_apply(conn, schema, ns):
    path = pathlib.Path(ns.file)
    if not path.is_file():
        sys.exit(f"No such file: {path}")
    code = run_apply(conn, schema, ns, path.read_text(encoding="utf-8"))
    if code:
        sys.exit(code)


def run_restore(conn, schema, ns):
    """Restore is itself a write: feed the saved statement back through the
    same six steps. There is no second write path and no silent restore."""
    e = read_journal_entry(ns.project_root, ns.id)
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

ROUTE  build/change with open decisions -> pythia-spec asks FIRST (questions
       mandatory; written spec/plan offered, skippable). explain -> explore.
       review -> review. landing a change -> apply.
LEARN  impact before any change. Ask the live database, never a dump. Read
       .pythia/conventions.md if present. List connections with `pythia
       connections` - never open connections.json (it holds passwords).
ASK    relay apply previews verbatim and wait for a real yes. >=10 dependents
       or cross-schema: show the developer before writing code. Refusals and
       exit 3 (written-but-broken) are relayed, never routed around. Do not
       read exit codes through a pipe.
DO     writes go through `pythia apply` only - snapshot, token, verify. The
       full contract: `pythia guide`.
"""


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
  how it is done here     similar · history            neighbours to imitate, versions that exist

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
One door for writes: snapshot -> impact -> preview -> token -> apply ->
verify -> report.

  apply                   the six-step write; --confirm binds to the preview
  journal restore         undo, through the same six steps
  unistr                  exact non-ASCII literals for what you are writing
  install · agent-user · guide    setting the harness itself up

The CLI enforces the gates: headless --yes is refused, policy cannot be
loosened without a human at a terminal, the snapshot cannot be switched off.

using-pythia routes to the right skill before any action; the skills
carry the full method (pythia-spec, -explore, -impact, -conventions, -write,
-apply, -review, -setup, -skill-author). No skill support on this platform?
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
    path.write_text(CONNECTIONS_TEMPLATE, encoding="utf-8")
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
    """No-Node fallback: copy the wheel-bundled pack into
    <base>/.claude/skills/ ONLY — the directory Claude Code reliably reads
    in both scopes (field evidence: a project's .agents/skills is invisible
    to some Claude Code versions, and a second copy doubles every menu
    entry). base_dir is the project root, or the home directory for -g.

    Both destinations are cleared link-first before the copy. An earlier
    `npx skills add` leaves .claude/skills/<name> as a symlink into
    .agents/skills/<name>; copying into that symlink would write through to
    its target, which the .agents cleanup then deletes — leaving a dangling
    link and no pack at all. Only the pack's own names are touched; other
    skills in those directories are left alone."""
    import shutil
    base = pathlib.Path(base_dir)
    dest_root = base / ".claude" / "skills"
    for pack in sorted(SKILLS_DIR.iterdir()):
        if not (pack / "SKILL.md").is_file():
            continue
        _remove_link_first(dest_root / pack.name)
        _remove_link_first(base / ".agents" / "skills" / pack.name)
        shutil.copytree(pack, dest_root / pack.name)
    clean_legacy_skills(base_dir)
    return [dest_root]


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
        print("\nGlobal skills serve every project — per-project installs "
              "will now skip the skills step automatically.")
        return
    path, created = scaffold_config(ns.project_root)
    print(f"{'Created' if created else 'Kept existing'} {path}")
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
    print(f"\nNext: fill in {path}")
    print(f"Then: {invocation()} check")
    scripts_dir = installed_scripts_dir()
    hint = entry_point_hint(shutil.which("pythia"), scripts_dir,
                            in_stored_path=stored_path_has(scripts_dir))
    if hint:
        print()
        print(hint)
        offer_path_fix(scripts_dir, interactive,
                       getattr(ns, "add_to_path", False))


COMMANDS = {"check": cmd_check, "ls": cmd_ls, "src": cmd_src, "args": cmd_args,
            "ddl": cmd_ddl, "cols": cmd_cols, "grep": cmd_grep, "sql": cmd_sql,
            "invalid": cmd_invalid, "errors": cmd_errors, "deps": cmd_deps,
            "impact": cmd_impact, "similar": cmd_similar, "plscope": cmd_plscope,
            "policy": cmd_policy, "journal": cmd_journal, "apply": cmd_apply,
            "conventions": cmd_conventions, "guide": cmd_guide, "connections": cmd_connections, "install": cmd_install,
            "unistr": cmd_unistr, "agent-user": cmd_agent_user,
            "history": cmd_history}

NO_DB_COMMANDS = {"policy", "journal", "install", "unistr", "guide", "connections",
                  "history"}


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
        prog="pythia", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
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
    s = sub.add_parser("install", parents=[common()],
                       help="install the skill pack and scaffold .pythia/ config")
    s.add_argument("-g", "--global", dest="glob", action="store_true",
                   help="install the skill pack machine-wide (serves every "
                        "project; per-project installs then skip skills)")
    s.add_argument("--add-to-path", action="store_true", dest="add_to_path",
                   help="put the pythia command on your PATH without asking "
                        "(Windows; user PATH only)")
    s.add_argument("--source", default=DEFAULT_SKILLS_SOURCE,
                   help="skills repo for `npx skills add` "
                        f"(default {DEFAULT_SKILLS_SOURCE})")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    forbid_write_flag(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
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
            ns.command == "journal" and getattr(ns, "action", "") == "restore"):
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
