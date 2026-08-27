#!/usr/bin/env python3
"""Phase 1 self-checks for pythia.py — pure logic only, no database needed.

Run: python tests/test_phase1.py
"""
import datetime
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import pythia  # noqa: E402


def expect_exit(fn, *needles):
    """fn must raise SystemExit whose message contains every needle (case-insensitive)."""
    try:
        fn()
    except SystemExit as e:
        msg = str(e).lower()
        for n in needles:
            assert n.lower() in msg, f"exit message missing {n!r}: {e}"
        return
    raise AssertionError("expected SystemExit, none raised")


def test_readonly_gate():
    assert pythia.is_readonly_sql("select * from dual")
    assert pythia.is_readonly_sql("  WITH x AS (select 1 from dual) select * from x")
    assert not pythia.is_readonly_sql("update t set a = 1")
    assert not pythia.is_readonly_sql("delete from t")
    assert not pythia.is_readonly_sql("begin null; end;")
    assert not pythia.is_readonly_sql("create or replace procedure p as begin null; end;")


def test_write_flag_refused():
    # since Phase 3 the message points at the real write path instead of
    # claiming the tool is read-only (it no longer is)
    expect_exit(lambda: pythia.forbid_write_flag(["sql", "--write", "select 1"]),
                "no --write", "apply")
    pythia.forbid_write_flag(["sql", "select 1 from dual"])  # no flag -> no exit


def test_resolve_explicit_and_env_name():
    cfg = {"ALPHA": {"user": "a"}, "BETA": {"user": "b"}}
    root = pathlib.Path("C:/proj")
    name, c = pythia.resolve_connection(cfg, "beta", {}, root / "ALPHA", root)
    assert name == "BETA" and c["user"] == "b"  # explicit wins over path
    expect_exit(lambda: pythia.resolve_connection(cfg, "GAMMA", {}, root, root),
                "ALPHA", "BETA")  # unknown name -> error listing options, no guessing
    name, _ = pythia.resolve_connection(cfg, None, {"PYTHIA_CONNECTION": "alpha"}, root, root)
    assert name == "ALPHA"


def test_resolve_env_credentials():
    env = {"PYTHIA_USER": "u", "PYTHIA_PASSWORD": "p", "PYTHIA_DSN": "h:1521/svc",
           "PYTHIA_SCHEMA": "OWNER1"}
    name, c = pythia.resolve_connection(None, None, env, pathlib.Path("C:/x"), None)
    assert name == "env"
    assert c["user"] == "u" and c["password"] == "p" and c["dsn"] == "h:1521/svc"
    assert c["schema"] == "OWNER1"


def test_resolve_path_segment():
    cfg = {"ALPHA": {"user": "a"}, "BETA": {"user": "b"}}
    root = pathlib.Path("C:/proj")
    # segment directly under root decides, at any depth below it
    name, _ = pythia.resolve_connection(cfg, None, {}, root / "BETA" / "sub" / "deep", root)
    assert name == "BETA"
    # cwd == root -> nothing to infer from -> error, not a guess
    expect_exit(lambda: pythia.resolve_connection(cfg, None, {}, root, root), "--conn")
    # segment matches no connection -> error listing options
    expect_exit(lambda: pythia.resolve_connection(cfg, None, {}, root / "OTHER" / "x", root),
                "ALPHA", "BETA")
    # cwd outside root -> error
    expect_exit(lambda: pythia.resolve_connection(cfg, None, {}, pathlib.Path("D:/elsewhere"), root),
                "--conn")


def test_resolve_default_connection_when_path_gives_nothing():
    cfg = {"ALPHA": {"user": "a"}, "BETA": {"user": "b"}, "default": "BETA"}
    root = pathlib.Path("C:/proj")
    # standing at the root itself there is nothing to infer from, so the named
    # default wins — a choice the user wrote down, not a guess
    name, c = pythia.resolve_connection(cfg, None, {}, root, root)
    assert name == "BETA" and c["user"] == "b"
    # a path segment is more specific, so it still beats the default
    name, _ = pythia.resolve_connection(cfg, None, {}, root / "ALPHA" / "x", root)
    assert name == "ALPHA"
    # and --conn beats everything
    name, _ = pythia.resolve_connection(cfg, "ALPHA", {}, root, root)
    assert name == "ALPHA"
    # so does PYTHIA_CONNECTION
    name, _ = pythia.resolve_connection(cfg, None, {"PYTHIA_CONNECTION": "ALPHA"}, root, root)
    assert name == "ALPHA"
    # "default" is not itself offered as a connection to choose
    expect_exit(lambda: pythia.resolve_connection(cfg, "default", {}, root, root),
                "unknown connection")


def test_resolve_default_must_name_a_connection():
    root = pathlib.Path("C:/proj")
    # the shape mistake that is easy to make: a bare true instead of a name
    expect_exit(lambda: pythia.resolve_connection(
        {"ALPHA": {"user": "a"}, "default": True}, None, {}, root, root),
        "must name a connection", "\"default\": \"ALPHA\"")
    # naming something that does not exist
    expect_exit(lambda: pythia.resolve_connection(
        {"ALPHA": {"user": "a"}, "default": "NOPE"}, None, {}, root, root),
        "NOPE", "ALPHA")


def test_resolve_rejects_a_connection_that_is_not_an_object():
    expect_exit(lambda: pythia.resolve_connection(
        {"ALPHA": {"user": "a"}, "BETA": "oops"}, None, {}, pathlib.Path("C:/x"), None),
        "BETA", "object")


def test_resolve_points_a_per_entry_default_to_the_right_place():
    """The default used to go inside an entry; say so rather than ignoring it."""
    expect_exit(lambda: pythia.resolve_connection(
        {"ALPHA": {"user": "a"}, "BETA": {"user": "b", "default": True}},
        None, {}, pathlib.Path("C:/proj"), pathlib.Path("C:/proj")),
        "top level", "\"default\": \"BETA\"")


def test_resolve_error_says_how_to_set_a_default():
    cfg = {"ALPHA": {"user": "a"}, "BETA": {"user": "b"}}
    root = pathlib.Path("C:/proj")
    expect_exit(lambda: pythia.resolve_connection(cfg, None, {}, root, root),
                "--conn", "default")


def test_resolve_single_connection_shortcut():
    name, c = pythia.resolve_connection({"ONLY": {"user": "x"}}, None, {},
                                        pathlib.Path("C:/anywhere"), None)
    assert name == "ONLY" and c["user"] == "x"


def test_resolve_case_collision_rejected():
    cfg = {"Dev": {"user": "a"}, "DEV": {"user": "b"}}
    expect_exit(lambda: pythia.resolve_connection(cfg, "dev", {}, pathlib.Path("C:/x"), None),
                "collide")


def test_resolve_no_config_message():
    expect_exit(lambda: pythia.resolve_connection(None, None, {}, pathlib.Path("C:/x"), None),
                "connections.json", "PYTHIA_USER")


def test_find_config_upward():
    with tempfile.TemporaryDirectory() as td:
        proj = pathlib.Path(td) / "proj"
        (proj / ".pythia").mkdir(parents=True)
        (proj / ".pythia" / "connections.json").write_text(
            '{"DEV": {"user": "u"}}', encoding="utf-8")
        cwd = proj / "DEV" / "code"
        cwd.mkdir(parents=True)
        cfg, root = pythia.find_config(cwd, {})
        assert root == proj and cfg and "DEV" in cfg
        # PYTHIA_CONFIG points straight at a file
        alt = pathlib.Path(td) / "alt.json"
        alt.write_text('{"X": {"user": "u"}}', encoding="utf-8")
        cfg2, root2 = pythia.find_config(cwd, {"PYTHIA_CONFIG": str(alt)})
        assert cfg2 and "X" in cfg2 and root2 == alt.parent


def test_connect_failure_message_names_the_connection():
    msg = pythia.connect_failure_message(OSError("getaddrinfo failed"), "DEV")
    assert "DEV" in msg                 # which entry failed, out of many
    assert "getaddrinfo failed" in msg  # the real cause, not swallowed
    assert "credential" in msg.lower()  # points at what to check
    assert "Traceback" not in msg


def test_color_enabled_respects_humans_and_pipes():
    class Tty:
        def isatty(self):
            return True

    class Pipe:
        def isatty(self):
            return False

    assert pythia.color_enabled(Tty(), {}) is True
    assert pythia.color_enabled(Pipe(), {}) is False          # agents get plain text
    assert pythia.color_enabled(Tty(), {"NO_COLOR": "1"}) is False   # ambient opt-out
    assert pythia.color_enabled(Pipe(), {"FORCE_COLOR": "1"}) is True
    # an explicit opt-in outranks the ambient opt-out (per no-color.org,
    # explicit configuration overrides NO_COLOR)
    assert pythia.color_enabled(Pipe(), {"NO_COLOR": "1", "FORCE_COLOR": "1"}) is True


def test_banner_gradient_modes():
    # truecolor terminals get a smooth RGB gradient...
    assert "38;2;" in pythia.banner(True, {"COLORTERM": "truecolor"})
    # ...Windows Terminal advertises itself via WT_SESSION, not COLORTERM...
    assert "38;2;" in pythia.banner(True, {"WT_SESSION": "abc"})
    # ...everything else falls back to a 256-color gradient
    fallback = pythia.banner(True, {})
    assert "38;5;" in fallback and "38;2;" not in fallback
    # and agents piping output get nothing at all
    assert pythia.banner(False) == ""


def test_paint_wraps_only_when_enabled():
    assert pythia.paint("hi", "green", True) == "\x1b[32mhi\x1b[0m"
    assert pythia.paint("hi", "green", False) == "hi"
    assert pythia.paint("hi", None, True) == "hi"


def test_clip_rows():
    rows = [0, 1, 2, 3, 4]
    assert pythia.clip(rows, 5) == (rows, False)          # exactly at limit -> complete
    shown, trunc = pythia.clip(rows, 4)
    assert shown == [0, 1, 2, 3] and trunc                # over limit -> flagged
    assert pythia.clip(rows, 0) == (rows, False)          # 0 = no cap
    shown, trunc = pythia.clip(rows, 2, offset=2)
    assert shown == [2, 3] and trunc                      # window in the middle
    shown, trunc = pythia.clip(rows, 2, offset=3)
    assert shown == [3, 4] and not trunc                  # window reaches the end


def test_source_numbering():
    rows = [("PACKAGE", 1, "package p is\n"), ("PACKAGE", 2, "end;\n"),
            ("PACKAGE BODY", 1, "package body p is\n"), ("PACKAGE BODY", 2, "end;\n")]
    out = pythia.format_source(rows, raw=False)
    assert "-- PACKAGE BODY" in out            # unit header separates spec from body
    assert out.count("     1  ") == 2          # line numbers restart per unit
    assert "     2  end;" in out
    raw = pythia.format_source(rows, raw=True)
    assert raw == "package p is\nend;\npackage body p is\nend;\n"


def test_source_unit_filter():
    rows = [("PACKAGE", 1, "spec\n"), ("PACKAGE BODY", 1, "body\n")]
    assert pythia.filter_units(rows, body=True, spec=False) == [("PACKAGE BODY", 1, "body\n")]
    assert pythia.filter_units(rows, body=False, spec=True) == [("PACKAGE", 1, "spec\n")]
    assert pythia.filter_units(rows, body=False, spec=False) == rows


def test_to_unistr():
    r"""Ported from the field tool's self-check: Vietnamese exact, quote
    doubled per SQL (never \'), backslash doubled, beyond-BMP \U form."""
    u = pythia.to_unistr
    assert u("Nhóm không được để trống") == \
        r"unistr('Nh\00F3m kh\00F4ng \0111\01B0\1EE3c \0111\1EC3 tr\1ED1ng')"
    assert u("Mã bệnh") == r"unistr('M\00E3 b\1EC7nh')"
    assert u("O'Brien") == "unistr('O''Brien')"
    assert u("a\\b") == r"unistr('a\\b')"
    assert u("\U0001F600") == r"unistr('\U0001F600')"
    assert u("ABC 123") == "unistr('ABC 123')"


def test_agent_user_sql_is_least_privilege():
    sql = pythia.agent_user_sql("APP", "APP_AGENT", "x", exists=False)
    assert "CREATE USER APP_AGENT" in sql
    assert "GRANT CREATE SESSION TO APP_AGENT" in sql
    assert "GRANT CONNECT THROUGH APP_AGENT" in sql
    for bad in ("DBA TO", "RESOURCE TO", "ANY "):   # the whole point
        assert bad not in sql.upper().replace("-- ", ""), bad


def test_agent_password_shape():
    for _ in range(20):
        pw = pythia.agent_password()
        assert len(pw) >= 14 and pw[0].isalpha() and "#" in pw
        assert '"' not in pw and "'" not in pw      # safe inside quotes


def test_save_agent_connection_keeps_owner_and_sets_default():
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / ".pythia").mkdir()
        cfg = {"default": "dev",
               "dev": {"host": "h", "port": 1521, "service_name": "svc",
                       "user": "PYTHIA", "password": "ownerpw",
                       "schema": "PYTHIA"}}
        (root / ".pythia" / "connections.json").write_text(
            json.dumps(cfg), encoding="utf-8")
        name = pythia.save_agent_connection(root, "dev", cfg["dev"],
                                            "PYTHIA_AGENT", "agpw")
        out = json.loads((root / ".pythia" / "connections.json")
                         .read_text(encoding="utf-8"))
        assert name == "dev_agent" and out["default"] == "dev_agent"
        assert out["dev"]["user"] == "PYTHIA"          # owner untouched
        assert out["dev"]["password"] == "ownerpw"
        assert out["dev_agent"]["user"] == "pythia_agent[pythia]"
        assert out["dev_agent"]["password"] == "agpw"
        assert out["dev_agent"]["host"] == "h"         # connect details copied


def test_json_envelope():
    s = pythia.json_envelope("ls", "DEV", "OWNER1", ["A", "B"],
                             [(1, datetime.date(2026, 1, 2))], truncated=True)
    d = json.loads(s)
    assert d["ok"] is True and d["command"] == "ls"
    assert d["connection"] == "DEV" and d["schema"] == "OWNER1"
    assert d["truncated"] is True
    assert d["rows"] == [{"A": 1, "B": "2026-01-02"}]


def test_connect_failure_names_the_fix_for_common_oracle_errors():
    """Generic advice is useless for an error Oracle already diagnosed. The
    three that actually strand people each have one specific next step."""
    locked = pythia.connect_failure_message(
        Exception("ORA-28000: The account is locked."), "dev", user="agent[owner]")
    assert "ACCOUNT UNLOCK" in locked and "AGENT" in locked   # the real account
    assert "DBA" in locked or "superuser" in locked.lower()

    expired = pythia.connect_failure_message(
        Exception("ORA-28001: the password has expired"), "dev", user="agent")
    assert "PASSWORD EXPIRE" in expired or "new password" in expired.lower()

    wrong = pythia.connect_failure_message(
        Exception("ORA-01017: invalid username/password"), "dev", user="agent")
    assert "lock the account" in wrong.lower()   # warn before they retry into one

    plain = pythia.connect_failure_message(OSError("getaddrinfo failed"), "dev")
    assert "ORA-" not in plain and "dev" in plain     # unchanged for the rest


def test_oracle_account_taken_from_a_proxy_connect_string():
    assert pythia.authenticating_account("agent[owner]") == "AGENT"
    assert pythia.authenticating_account("plain_user") == "PLAIN_USER"
    assert pythia.authenticating_account(None) is None


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            # SystemExit is a BaseException: an unexpected sys.exit inside a test
            # would otherwise kill the run and silently skip everything after it.
            except (Exception, SystemExit) as e:  # noqa: BLE001 — keep going
                failed += 1
                print(f"FAIL {name}: {e!r}")
    if failed:
        sys.exit(f"{failed} test(s) failed")
    print("OK")


if __name__ == "__main__":
    main()
