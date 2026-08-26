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

QUERY_DIR = pathlib.Path(__file__).resolve().parent.parent / "queries"

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
}


# --- pure helpers (covered by tests/test_phase1.py) --------------------------

def is_readonly_sql(stmt):
    return bool(READONLY.match(stmt))


def invocation():
    """How to invoke this tool, exactly as the user actually ran it. Every
    printed command must be paste-able: a bare `pythia ...` is
    CommandNotFound for anyone running from source."""
    prog = sys.argv[0] or "pythia"
    if prog.lower().endswith(".py"):
        return f"python {prog}"
    return pathlib.Path(prog).name


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
Is rollback real?  (this table also appears in README.md and plsql-apply)
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

def connect_failure_message(exc, conn_name):
    """A failure to connect should say which entry failed and what to check —
    a driver stack trace tells the reader nothing actionable."""
    return (f"Could not connect using connection {conn_name!r}: {exc}\n"
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
        print(f"\n{warn}", file=sys.stderr)


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
    shown, truncated = clip(rows, ns.max_lines, ns.offset)
    emit_source(ns, shown, truncated, total)


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
    shown, truncated = clip(rows, ns.limit)
    if ns.json:
        print(json_envelope(ns.command, ns.conn_name, ns.schema, cols, shown, truncated,
                            summary=impact_summary(rows)))
        return
    if not rows:
        print(f"-- nothing depends on {ns.name.upper()} "
              f"(within {schema}, depth {ns.depth})")
        return
    sys.stdout.write(render_tree(shown, f"{schema}.{ns.name.upper()}"))
    if truncated:
        print(f"-- truncated at {len(shown)} rows (raise --limit, or --limit 0 for no cap)")
    print(impact_summary(rows))


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


def privilege_warning(conn, schema, conn_user):
    """One line, only when true. The policy file is an application-side fence;
    Oracle grants are the only layer that cannot be walked around, so say when
    this session is running with more power than the task needs."""
    _, rows = run_query(conn, load_query("session-privileges.sql"))
    dangerous = [r[0] for r in rows]
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
    confirmed = bool(ns.yes) or ns.confirm == token
    if ns.confirm and ns.confirm != token:
        sys.exit("The confirmation token does not match: the file or the "
                 "database object changed since that preview. Preview again:\n"
                 f"  {invocation()} apply {ns.file}")

    _, inv_rows = run_query(conn, load_query("invalid-objects.sql"), {"s": schema})
    invalid_before = [(r[0], r[1]) for r in inv_rows]
    meta = {"schema": schema, "connection": ns.conn_name, "group": group,
            "token": token, "applied": False,
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
    if ns.json:
        print(json.dumps({"ok": True, "object": name, "type": otype,
                          "created": created, "changed_lines": changed,
                          "summary": summary, "warning": warn, "token": token,
                          "journal": entry, "will_apply": confirmed}))
    else:
        if created:
            head = "new object"
        elif changed == 0:
            head = "no source change (recompile)"
        else:
            head = f"{changed} lines changed"
        print(f"\n  {name} ({otype}) in {schema} — {head}")
        if summary:
            print(f"  {summary.lstrip('- ')}")
        if warn:
            print(f"\n  {warn}")
        if diff_text:
            print()
            for ln in diff_text.splitlines():
                print(f"  {ln}")
        print(f"\n  Snapshot saved: {journal_root(ns.project_root) / entry}")
        if not confirmed:
            print(f"\n  To apply:\n    {invocation()} apply {ns.file} --confirm {token}")
    if not confirmed:
        return 0

    # 4. APPLY
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
                          "exit": 0 if ok else 3}))
    else:
        if ok:
            print(f"\n  Applied {name} ({otype}).")
            print("  Compiled clean. No new INVALID objects.")
        else:
            if own_errors:
                print(f"\n  Applied {name} ({otype}) — but it did not compile cleanly:")
                for r in own_errors:
                    print(f"    {r[3]}:{r[4]} {r[5]} {str(r[6]).strip()}")
            else:
                print(f"\n  Applied {name} ({otype}) — it compiled, but broke "
                      "other objects:")
            if broke:
                print(f"  {len(broke)} objects were VALID before and are INVALID now:")
                for n2, t2 in broke:
                    print(f"    {n2} ({t2})")
        undo = "dropping it (it did not exist before)" if created else None
        print(f"\n  To undo{' — note: undo means ' + undo if undo else ''}:")
        print(f"    {invocation()} journal restore {entry}")
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
    if not ns.id:
        sys.exit(f"Usage: {invocation()} journal "
                 "{list | show <id> | diff <id> | export <id> | restore <id>}")
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


COMMANDS = {"check": cmd_check, "ls": cmd_ls, "src": cmd_src, "args": cmd_args,
            "ddl": cmd_ddl, "cols": cmd_cols, "grep": cmd_grep, "sql": cmd_sql,
            "invalid": cmd_invalid, "errors": cmd_errors, "deps": cmd_deps,
            "impact": cmd_impact, "similar": cmd_similar, "plscope": cmd_plscope,
            "policy": cmd_policy, "journal": cmd_journal, "apply": cmd_apply}

NO_DB_COMMANDS = {"policy", "journal"}


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
                   choices=["list", "show", "diff", "export", "restore"],
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
    cwd = pathlib.Path.cwd()
    cfg, root = find_config(cwd, os.environ)
    ns.project_root = root if root is not None else cwd
    if ns.command in NO_DB_COMMANDS and not (
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
        sys.exit(connect_failure_message(e, name))
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
