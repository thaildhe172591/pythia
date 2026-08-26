#!/usr/bin/env python3
"""pythia — ask an Oracle Database directly instead of reading stale dumps.

This build is READ-ONLY: there is no write mode and no --write flag.
Change PL/SQL by editing files and applying them yourself; a controlled
write workflow (`apply`) is planned but not part of this build.

Connection resolution order:
  1. --conn NAME
  2. PYTHIA_CONNECTION                (name of an entry in connections.json)
  3. PYTHIA_USER / PYTHIA_PASSWORD / PYTHIA_DSN   (+ optional PYTHIA_SCHEMA)
  4. .pythia/connections.json, searched upward from the current directory
     (override the file path with PYTHIA_CONFIG):
       - a single entry is used as-is
       - with several entries, the path segment directly under the project
         root picks one (root/DEV/anything -> DEV); anything ambiguous is
         an error, never a guess

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
    "dependencies.sql": {"s", "n", "depth"},
    "impact.sql": {"s", "n", "depth"},
    "similar-candidates.sql": {"s"},
}


# --- pure helpers (covered by tests/test_phase1.py) --------------------------

def is_readonly_sql(stmt):
    return bool(READONLY.match(stmt))


def forbid_write_flag(argv):
    if "--write" in argv:
        sys.exit("pythia is read-only in this build: there is no --write mode.\n"
                 "Edit PL/SQL in files and apply changes yourself; a controlled\n"
                 "write workflow (`apply`) is planned but not available yet.")


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
    credentials, then the config file (single entry, or the path segment
    directly under the project root). Ambiguity is an error, never a guess."""
    lookup = {}
    for k, v in (cfg or {}).items():
        if k.upper() in lookup:
            sys.exit(f"Connection names collide ignoring case in {CONFIG_NAME}: "
                     f"{lookup[k.upper()][0]!r} vs {k!r}")
        lookup[k.upper()] = (k, v)
    names = ", ".join(sorted(k for k, _ in lookup.values()))

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
            sys.exit(f"Path segment {seg[0]!r} matches no connection. "
                     f"Available: {names}. Use --conn NAME.")
    sys.exit(f"Cannot infer a connection from {cwd}. Available: {names}. Use --conn NAME.")


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


def json_envelope(command, connection, schema, cols, rows, truncated, **extra):
    payload = {"ok": True, "command": command, "connection": connection,
               "schema": schema, "rows": [dict(zip(cols, r)) for r in rows],
               "truncated": bool(truncated), **extra}
    return json.dumps(payload, default=str, ensure_ascii=False)


# --- database access ---------------------------------------------------------

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


def acquire(pool):
    conn = pool.acquire()
    with conn.cursor() as cur:                    # defence 2: Oracle itself rejects
        cur.execute("set transaction read only")  # DML/DDL in this transaction.
    return conn                                   # NOT inherited by autonomous
    #                                               transactions inside called PL/SQL.


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


def cmd_ls(conn, schema, ns):
    cols, rows = run_query(conn, """
        select object_name, object_type, status, to_char(last_ddl_time,'yyyy-mm-dd') last_ddl
        from all_objects where owner = :s and object_name like upper(:p)
        order by object_type, object_name fetch first :n rows only""",
        {"s": schema, "p": ns.pattern, "n": fetch_n(ns.limit)})
    rows, truncated = clip(rows, ns.limit)
    emit_table(ns, cols, rows, truncated)


def cmd_src(conn, schema, ns):
    _, rows = run_query(conn, """
        select type, line, text from all_source
        where owner = :s and name = upper(:n)
        order by type, line""", {"s": schema, "n": ns.name})
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
                           {"s": schema, "n": ns.name, "depth": ns.depth})
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


COMMANDS = {"check": cmd_check, "ls": cmd_ls, "src": cmd_src, "args": cmd_args,
            "ddl": cmd_ddl, "cols": cmd_cols, "grep": cmd_grep, "sql": cmd_sql,
            "invalid": cmd_invalid, "errors": cmd_errors, "deps": cmd_deps,
            "impact": cmd_impact, "similar": cmd_similar}


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
    s = sub.add_parser("impact", parents=[common()],
                       help="what depends on an object — run this before changing it")
    s.add_argument("name")
    s.add_argument("--depth", type=int, default=3,
                   help="levels to walk (default 3)")
    s = sub.add_parser("similar", parents=[common()],
                       help="programs named like this one — copy their conventions")
    s.add_argument("name")
    s.set_defaults(limit=20)
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
    name, c = resolve_connection(cfg, ns.conn, os.environ, cwd, root)
    ns.conn_name = name
    ns.schema = (c.get("schema") or c["user"]).upper()
    print(f"-- connection={name} schema={ns.schema}", file=sys.stderr)
    try:
        import oracledb
    except ModuleNotFoundError:
        sys.exit("The 'oracledb' package is required to connect: pip install oracledb")
    pool = open_pool(c)
    try:
        COMMANDS[ns.command](acquire(pool), ns.schema, ns)
    except oracledb.Error as e:
        sys.exit(f"Oracle error: {e}")
    finally:
        pool.close(force=True)


if __name__ == "__main__":
    main()
