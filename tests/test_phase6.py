#!/usr/bin/env python3
"""Phase 6 self-checks — scripts, comments, lint, autopilot, deploy, hooks,
doctor. No database: the write path runs against the same fake connection
tests/test_phase3.py proves the safety properties with.

Run: python tests/test_phase6.py
"""
import contextlib
import datetime
import io
import json
import os
import pathlib
import sys
import tempfile

os.environ.setdefault("PYTHIA_CI", "1")   # the suites are headless by design
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))
import pythia  # noqa: E402
import test_phase3 as p3  # noqa: E402  (fakes only; its tests are not re-run)
import test_install as ti  # noqa: E402  (git helpers only; its tests are not re-run)

FakeConn, base_script, apply_ns, expect_exit = (
    p3.FakeConn, p3.base_script, p3.apply_ns, p3.expect_exit)


def quiet(fn, *a, **kw):
    """Run fn with stdout captured; returns (result, output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


def policy(root, **groups):
    d = pathlib.Path(root) / ".pythia"
    d.mkdir(exist_ok=True)
    (d / "policy.json").write_text(json.dumps(groups), encoding="utf-8")


def last_meta(root):
    return pythia.read_journal_entry(
        root, pythia.list_journal_entries(root)[0])["meta"]


def executed_ddl(conn, prefix):
    return [s for s, _ in conn.executed if s.lstrip().lower().startswith(prefix)]


# --- splitting scripts ---------------------------------------------------------

SCRIPT = """-- release 12
set define off
prompt creating the order table
create table t_order (
  id   number constraint pk_order primary key,
  note varchar2(100 char) default 'a;b'
);
comment on table t_order is 'Orders; one row each';
comment on column t_order.id is q'[it's the id; really]';
create or replace package body pkg_order as
  procedure p is begin null; end;
end pkg_order;
/
insert into t_order (id) values (1); insert into t_order (id) values (2);
/* a block comment ; with a semicolon */
drop table t_x purge;
"""


def test_split_script_follows_sqlplus_semantics():
    st, skipped = pythia.split_script(SCRIPT)
    groups = [pythia.classify(s["text"]) for s in st]
    assert groups == ["structural", "comments", "comments", "plsql_source",
                      "data_dml", "data_dml", "structural"], groups
    assert [s["line"] for s in st] == [4, 8, 9, 10, 14, 14, 15]
    assert st[0]["text"].endswith(")")              # the ; is not kept for SQL
    assert "'a;b'" in st[0]["text"]                 # ; inside a literal stays
    assert "q'[it's the id; really]'" in st[2]["text"]
    assert st[3]["text"].rstrip().endswith("end pkg_order;")   # block keeps END;
    assert [x["line"] for x in skipped] == [2, 3]   # directives, not SQL


def test_split_script_refuses_includes_and_keeps_line_numbers_exact():
    expect_exit(lambda: pythia.split_script("@other.sql\n"), "include")
    st, _ = pythia.split_script("\n\n-- head\ncreate table a (x number)\n;\n")
    assert st[0]["line"] == 3 and pythia.classify(st[0]["text"]) == "structural"
    # a block ends only at a lone slash, even with ; and / inside literals
    st, _ = pythia.split_script(
        "create or replace procedure p as\n  v varchar2(9) := '/';\nbegin\n"
        "  null;\nend;\n/\nselect 1 from dual;\n")
    assert len(st) == 2 and "'/'" in st[0]["text"]


def test_mask_sql_blanks_literals_and_comments_at_the_same_length():
    src = "x := 'a''b' || q'{c'd}' -- tail\n/* y */ z"
    m = pythia.mask_sql(src)
    assert len(m) == len(src) and m.count("\n") == 1
    assert "a" not in m.split(":=")[1].split("||")[0]
    assert "tail" not in m and "y" not in m and m.rstrip().endswith("z")


# --- the comments group -----------------------------------------------------------

def test_comment_on_is_its_own_group_with_a_parsed_target():
    assert pythia.classify("COMMENT ON COLUMN t_order.id IS 'x'") == "comments"
    assert pythia.classify("comment on table app.t_order is 'x'") == "comments"
    assert pythia.classify("comment on materialized view mv is 'x'") == "structural"
    assert pythia.parse_comment_target(
        'comment on column app.t_order."Mixed" is \'x\'') == (
        "column", "APP", "T_ORDER", "Mixed")
    assert pythia.parse_comment_target("comment on table t is 'x'") == (
        "table", None, "T", None)
    assert pythia.render_comment("column", "T", "Mixed", "it's") == \
        "COMMENT ON COLUMN T.\"Mixed\" IS 'it''s'\n"
    assert pythia.POLICY_DEFAULTS["comments"] == "confirm"


def test_prepare_statement_reads_a_semicolon_in_a_literal_as_text():
    out = pythia.prepare_statement("comment on table t is 'a; b';\n", "comments")
    assert out == "comment on table t is 'a; b'"
    expect_exit(lambda: pythia.prepare_statement(
        "comment on table t is 'a'; comment on table u is 'b';", "comments"),
        "one statement")


def comment_script(old="Old text"):
    s = base_script()
    s["from all_col_comments"] = ([("COMMENTS",)], [(old,)] if old is not None else [])
    return s


def test_comment_preview_snapshots_the_old_comment_and_restores_it():
    with tempfile.TemporaryDirectory() as td:
        text = "COMMENT ON COLUMN t_order.note IS 'Free text from the customer'\n"
        code, out = quiet(pythia.run_apply, FakeConn(comment_script()), "APP",
                          apply_ns(td, file="c.sql"), text)
        assert code == 0
        e = pythia.read_journal_entry(td, pythia.list_journal_entries(td)[0])
        assert e["meta"]["group"] == "comments"
        assert e["meta"]["object"] == "T_ORDER.NOTE"
        assert e["before"] == "Old text"
        assert e["restore"] == "COMMENT ON COLUMN T_ORDER.NOTE IS 'Old text'\n"
        assert "-COMMENT ON COLUMN T_ORDER.NOTE IS 'Old text'" in out


def test_comment_apply_writes_and_says_how_to_undo():
    with tempfile.TemporaryDirectory() as td:
        text = "COMMENT ON COLUMN t_order.note IS 'New'\n"
        quiet(pythia.run_apply, FakeConn(comment_script()), "APP",
              apply_ns(td, file="c.sql"), text)
        tok = last_meta(td)["token"]
        pythia.mint_grant(td, tok, "DEV")
        conn = FakeConn(comment_script())
        code, out = quiet(pythia.run_apply, conn, "APP",
                          apply_ns(td, file="c.sql", confirm=tok), text)
        assert code == 0
        assert executed_ddl(conn, "comment on column")
        assert "journal restore" in out and "previous comment" in out


def test_comment_card_promises_a_real_undo():
    with tempfile.TemporaryDirectory() as td:
        quiet(pythia.run_apply, FakeConn(comment_script()), "APP",
              apply_ns(td, file="c.sql"),
              "COMMENT ON TABLE t_order IS 'Orders'\n")
        _, _, body = pythia.approval_card(td, last_meta(td)["token"])
        text = "\n".join(body)
        assert "snapshotted" in text and "no snapshot" not in text


# --- destructive statements ---------------------------------------------------

def test_destructive_reason_names_what_cannot_come_back():
    d = pythia.destructive_reason
    assert "Recycle Bin" in d("DROP TABLE t PURGE", "structural")
    assert d("drop table t", "structural")
    assert d("truncate table t", "structural")
    assert d("alter table t drop column x", "structural")
    assert d("alter table t rename column a to b", "structural")
    assert d("alter table t set unused (x)", "structural")
    assert d("delete from t", "data_dml") and d("update t set x = 1", "data_dml")
    assert d("revoke select on t from u", "grants")
    assert d("alter table t add (x number)", "structural") is None
    assert d("create table t (x number)", "structural") is None
    assert d("delete from t where id = 1", "data_dml") is None
    assert d("create or replace procedure p as begin null; end;",
             "plsql_source") is None
    assert d("alter table t add (note varchar2(9) default 'drop')",
             "structural") is None             # a literal is not a keyword


def test_destructive_covers_what_review_found_missing():
    d = pythia.destructive_reason
    assert d("delete from t_order where 1 = 1", "data_dml")
    assert d("update t set x = 1 where (1=1)", "data_dml")
    assert d("create or replace synonym s for other.x", "structural")
    assert d("alter sequence s_order restart start with 1", "structural")
    assert d("alter user app account lock", "structural")
    assert d("grant select on t to public", "grants")
    assert d("grant select any table to app_ro", "grants")
    assert d("grant select on t to app_ro with grant option", "grants")
    assert d("grant dba to app", "grants")
    assert d("grant select on t to app_ro", "grants") is None
    # a comment inside the header does not turn a view into structural DDL
    assert pythia.classify("CREATE OR REPLACE /* c */ VIEW v AS SELECT 1 x "
                           "FROM dual") == "plsql_source"
    assert pythia.parse_object("create or replace /* c */ view v as select 1 "
                               "from dual")[:2] == ("VIEW", "V")


def test_lint_settings_cannot_open_security_relevant_grants():
    """conventions.json is a file the agent is told to edit. Turning a lint
    rule off must not let a lease land GRANT ... TO PUBLIC."""
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("grants",))
        ok, _, why = pythia.autopilot_check(td, "DEV", "grants",
                                            "grant select on t to public")
        assert not ok and "PUBLIC" in why


def test_a_big_row_set_waits_for_a_person_even_under_a_lease():
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("data_dml",))
        conn = FakeConn(p3.dml_script(count=5000), rowcount=5000)
        _, out = quiet(pythia.run_apply, conn, "APP", p3.dml_ns(td), p3.DML_FILE)
        assert p3.wrote_dml(conn) == [] and "5000 rows" in out
        conn = FakeConn(p3.dml_script(count=2), rowcount=2)
        code, _ = quiet(pythia.run_apply, conn, "APP", p3.dml_ns(td), p3.DML_FILE)
        assert code == 0 and p3.wrote_dml(conn) and conn.commits == 1


def test_a_lease_stops_covering_a_connection_marked_protected_later():
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        d = pathlib.Path(td) / ".pythia"
        (d / "connections.json").write_text(json.dumps(
            {"DEV": {"user": "a", "protected": True}}), encoding="utf-8")
        ok, _, why = pythia.autopilot_check(td, "DEV", "plsql_source", p3.NEW_FILE)
        assert not ok and "protected" in why
        expect_exit(lambda: pythia.new_lease_request(td, "env", {}), "named connection")


def test_structural_writes_are_checked_for_broken_dependents():
    """Found in review: only plsql_source compared INVALID before and after,
    so an ALTER TABLE that broke dependents reported exit 0 and the lease
    never paused."""
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("structural",))
        conn = FakeConn(base_script(invalid_after=[("X_ALREADY_BROKEN", "PROCEDURE"),
                                                   ("P_USES_ORDER", "PROCEDURE")]))
        code, out = quiet(pythia.run_apply, conn, "APP", apply_ns(td, file="a.sql"),
                          "alter table t_order modify (note varchar2(50 char))\n")
        assert code == 3 and "INVALID" in out
        assert pythia.lease_status(pythia.read_lease(td)) == "paused"


def test_quoted_identifiers_cannot_escape_the_journal():
    with tempfile.TemporaryDirectory() as td:
        eid = pythia.write_journal_entry(td, "COMMENT", 'T_ORDER.a/../../x:y', "", "",
                                         {})
        assert "/" not in eid and ":" not in eid
        assert (pythia.journal_root(td) / eid / "meta.json").is_file()


def test_the_lease_card_is_the_same_whoever_renders_it():
    """Found in review: the card embedded invocation(), which differs between
    the agent's `approve --card` and the hook's `python -m pythia`, so a real
    Approve was thrown away as a paraphrase."""
    with tempfile.TemporaryDirectory() as td:
        req = pythia.new_lease_request(td, "DEV", {}, note="build it")
        text = "\n".join(pythia.lease_card(req, td))
        assert pythia.invocation() not in text
        assert "as the agent described it" in text


def test_a_destructive_statement_is_flagged_in_preview_and_card():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        _, out = quiet(pythia.run_apply, FakeConn(base_script()), "APP",
                       apply_ns(td, file="d.sql"), "drop table t_old\n")
        assert "DESTRUCTIVE" in out
        _, _, body = pythia.approval_card(td, last_meta(td)["token"])
        assert any("DESTRUCTIVE" in ln for ln in body)


# --- lint -------------------------------------------------------------------------

def rules(findings):
    return {f[0] if isinstance(f, tuple) else f["rule"] for f in findings}


def test_lint_catches_the_plsql_antipatterns():
    src = """CREATE OR REPLACE PROCEDURE p_bad(p_out IN OUT CLOB) AS
  v_name VARCHAR2(50);
BEGIN
  FOR r IN (SELECT id FROM t_order WHERE status = 'NEW') LOOP
    UPDATE t_order SET status = 'DONE' WHERE id = r.id;
    COMMIT;
  END LOOP;
  EXECUTE IMMEDIATE 'DELETE FROM t_log WHERE id = ' || v_name;
  IF v_name = '' THEN NULL; END IF;
  RAISE_APPLICATION_ERROR(-1, 'x');
  SELECT * INTO v_name FROM dual;
  v_name := 'Số tiền';
EXCEPTION
  WHEN OTHERS THEN NULL;
END p_bad;
"""
    got = rules(pythia.lint_statement(src, "plsql_source"))
    for r in ("when-others-null", "dynamic-sql-concat", "commit-in-loop",
              "row-by-row-dml", "empty-string-compare", "raise-app-error-range",
              "select-star", "non-ascii-literal", "hardcoded-size",
              "inout-lob-nocopy"):
        assert r in got, f"lint blind to {r}: {sorted(got)}"


def test_lint_stays_quiet_on_clean_code():
    src = """CREATE OR REPLACE PROCEDURE p_good(p_id IN t_order.id%TYPE) AS
  v_name t_order.note%TYPE;
BEGIN
  SELECT note INTO v_name FROM t_order WHERE id = p_id;
  EXECUTE IMMEDIATE 'DELETE FROM t_log WHERE id = :1' USING p_id;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RAISE_APPLICATION_ERROR(-20001, 'order not found');
  WHEN OTHERS THEN
    log_error(SQLCODE, SQLERRM);
    RAISE;
END p_good;
"""
    assert pythia.lint_statement(src, "plsql_source") == []


def test_lint_knows_the_sanctioned_forms():
    assert "dynamic-sql-concat" not in rules(pythia.lint_statement(
        "create or replace procedure p(in_t varchar2) as begin "
        "execute immediate 'truncate table ' || dbms_assert.sql_object_name(in_t); "
        "end;", "plsql_source"))
    trig = ("create or replace trigger trg_x after insert on t for each row "
            "begin insert into t_log values (1); commit; end;")
    assert "commit-in-trigger" in rules(pythia.lint_statement(trig, "plsql_source"))
    body = ("create or replace package body pkg as procedure p is begin "
            "commit; end; end;")
    assert "commit-in-trigger" not in rules(pythia.lint_statement(body, "plsql_source"))


def test_lint_reads_tables_against_the_ddl_standards():
    f = rules(pythia.lint_statement(
        "create table t_note (id number, body varchar2(200), "
        "ref_id number references t_order(id))", "structural"))
    assert {"table-no-pk", "constraint-unnamed",
            "varchar2-byte-semantics"} <= f
    f = rules(pythia.lint_statement(
        "alter table t_order add (status varchar2(10 char) not null)",
        "structural"))
    assert "add-not-null-no-default" in f
    assert "add-not-null-no-default" not in rules(pythia.lint_statement(
        "alter table t_order add (status varchar2(10 char) default 'N' not null)",
        "structural"))
    assert "dml-no-where" in rules(pythia.lint_statement("delete from t", "data_dml"))
    assert "grant-to-public" in rules(pythia.lint_statement(
        "grant select on t to public", "grants"))


def test_lint_script_wants_every_new_column_commented_and_fks_indexed():
    script = """create table t_line (
  id       number constraint pk_line primary key,
  order_id number constraint fk_line_order references t_order(id),
  qty      number,
  constraint fk_line_order2 foreign key (order_id) references t_order(id)
);
comment on table t_line is 'Order lines';
comment on column t_line.id is 'Key';
"""
    f = pythia.lint_script(script)
    miss = [x for x in f if x["rule"] == "comment-missing"]
    assert miss and "ORDER_ID" in miss[0]["message"] and "QTY" in miss[0]["message"]
    assert any(x["rule"] == "fk-no-index" for x in f)
    fixed = script + ("comment on column t_line.order_id is 'Order';\n"
                      "comment on column t_line.qty is 'Quantity';\n"
                      "create index ix_line_order on t_line (order_id);\n")
    assert not [x for x in pythia.lint_script(fixed)
                if x["rule"] in ("comment-missing", "fk-no-index")]


def test_the_lead_tunes_lint_through_conventions_json():
    conv = {"naming": {"TABLE": "^T_[A-Z0-9_]+$"},
            "lint": {"rules": {"varchar2-byte-semantics": "error",
                               "table-no-pk": "off"},
                     "required_columns": ["CREATED_BY", "CREATED_AT"],
                     "param_prefix": "^P_",
                     "forbid": [{"id": "no-sysdate", "pattern": r"\bSYSDATE\b",
                                 "message": "use SYSTIMESTAMP",
                                 "severity": "error"}]}}
    f = pythia.lint_findings("create table orders (id number, n varchar2(9), "
                             "d date default sysdate)", "structural", conv)
    by = {x["rule"]: x for x in f}
    assert by["varchar2-byte-semantics"]["severity"] == "error"
    assert "table-no-pk" not in by
    assert "CREATED_BY" in by["required-column-missing"]["message"]
    assert by["forbid:no-sysdate"]["severity"] == "error"
    assert "naming" in by
    f = pythia.lint_findings("create or replace procedure p_x(id number) as "
                             "begin null; end;", "plsql_source", conv)
    assert any(x["rule"] == "param-prefix" for x in f)
    expect_exit(lambda: pythia.lint_config({"lint": {"rules": {"nope": "error"}}}),
                "nope")
    expect_exit(lambda: pythia.lint_config({"lint": {"typo": 1}}), "typo")
    expect_exit(lambda: pythia.lint_config({"lint": {"forbid": [{}]}}), "id")


def test_conventions_json_accepts_a_lint_block():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / ".pythia"
        d.mkdir()
        (d / "conventions.json").write_text(json.dumps(
            {"naming": {}, "lint": {"require_comments": "error"}}),
            encoding="utf-8")
        conv = pythia.load_conventions(td)
        assert conv["lint"]["require_comments"] == "error"


def test_apply_preview_shows_lint_findings():
    with tempfile.TemporaryDirectory() as td:
        bad = ("CREATE OR REPLACE PACKAGE BODY pkg_order AS\n"
               "  PROCEDURE p IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;\n"
               "END;\n/\n")
        _, out = quiet(pythia.run_apply, FakeConn(base_script()), "APP",
                       apply_ns(td, file="b.sql"), bad)
        assert "when-others-null" in out and "lint:" in out
        assert last_meta(td)["lint"][0]["rule"] == "when-others-null"


def test_cmd_lint_exits_2_on_errors():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "x.sql"
        f.write_text("delete from t_order;\n", encoding="utf-8")
        ns = argparse.Namespace(paths=[str(f)], json=True, project_root=td,
                                color=False)
        try:
            _, out = quiet(pythia.cmd_lint, None, None, ns)
            raise AssertionError("expected exit 2")
        except SystemExit as e:
            assert e.code == 2
        f.write_text("delete from t_order where id = 1;\n", encoding="utf-8")
        _, out = quiet(pythia.cmd_lint, None, None, ns)
        assert json.loads(out)["errors"] == 0


# --- autopilot ----------------------------------------------------------------------

NOW = datetime.datetime(2026, 10, 14, 9, 0, 0)


def lease(td, groups=("plsql_source", "comments"), conn="DEV", **kw):
    req = pythia.new_lease_request(td, conn, {}, groups=list(groups), **kw)
    return pythia.activate_lease(td, req, "console")


def test_a_protected_connection_is_never_leased():
    with tempfile.TemporaryDirectory() as td:
        expect_exit(lambda: pythia.new_lease_request(
            td, "PROD", {"protected": True}), "protected")
        expect_exit(lambda: pythia.new_lease_request(
            td, "LIVE", {"env": "production"}), "protected")
        expect_exit(lambda: pythia.new_lease_request(td, "DEV", {}, hours=48),
                    "hours")


def test_the_lease_card_names_every_consequence():
    with tempfile.TemporaryDirectory() as td:
        req = pythia.new_lease_request(td, "DEV", {}, groups=["plsql_source",
                                                              "structural"],
                                       note="build the order module")
        text = "\n".join(pythia.lease_card(req, td))
        assert "DEV" in text and "8 hours" in text and "200 writes" in text
        assert "NO real undo" in text                 # structural, honestly
        assert "overrides it" in text                 # structural is deny
        assert "still need your approval" in text     # destructive excluded
        assert "build the order module" in text
        assert len(req["token"]) == 8


def test_an_active_lease_lands_a_covered_write_without_confirm():
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        conn = FakeConn(base_script())
        code, out = quiet(pythia.run_apply, conn, "APP",
                          apply_ns(td, file="b.sql"), p3.NEW_FILE)
        assert code == 0 and executed_ddl(conn, "create or replace")
        m = last_meta(td)
        assert m["confirmed_via"] == "autopilot" and m["applied"]
        assert m["autopilot"] == pythia.read_lease(td)["token"]
        assert pythia.read_lease(td)["writes"] == 1
        assert "autopilot" in out


def test_a_lease_never_covers_another_connection_group_or_destruction():
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("plsql_source", "structural"))
        policy(td, structural="confirm")
        for ns_kw, text, why in (
                ({"conn_name": "TEST"}, p3.NEW_FILE, "connection"),
                ({}, "grant select on t to u\n", "does not cover"),
                ({}, "drop table t_old\n", "destructive")):
            conn = FakeConn(base_script())
            if why == "does not cover":
                policy(td, structural="confirm", grants="confirm")
            _, out = quiet(pythia.run_apply, conn, "APP",
                           apply_ns(td, file="x.sql", **ns_kw), text)
            assert not executed_ddl(conn, "create or replace") and \
                not executed_ddl(conn, "drop") and not executed_ddl(conn, "grant")
            assert why in out, out
        assert pythia.read_lease(td)["writes"] == 0


def test_lint_errors_are_not_landed_by_autopilot():
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        bad = ("CREATE OR REPLACE PACKAGE BODY pkg_order AS\n"
               "  PROCEDURE p IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;\n"
               "END;\n/\n")
        conn = FakeConn(base_script())
        _, out = quiet(pythia.run_apply, conn, "APP",
                       apply_ns(td, file="b.sql"), bad)
        assert not executed_ddl(conn, "create or replace")
        assert "lint error" in out


def test_a_leased_group_passes_a_deny_policy_and_an_unleased_one_does_not():
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("plsql_source", "structural"))
        conn = FakeConn(base_script())
        code, _ = quiet(pythia.run_apply, conn, "APP", apply_ns(td, file="t.sql"),
                        "create table t_new (id number constraint pk_new "
                        "primary key)\n")
        assert code == 0 and executed_ddl(conn, "create table")
        expect_exit(lambda: quiet(pythia.run_apply, FakeConn(base_script()),
                                  "APP", apply_ns(td, file="g.sql"),
                                  "grant select on t to u\n"), "deny")


def test_a_destructive_statement_under_a_lease_waits_for_its_own_approval():
    """The card says destructive statements still need the developer's
    approval each time — so the lease's deny override must let them be
    previewed and approved one by one, while never landing them itself."""
    with tempfile.TemporaryDirectory() as td:
        lease(td, groups=("structural",))           # structural is deny by policy
        conn = FakeConn(base_script())
        code, out = quiet(pythia.run_apply, conn, "APP",
                          apply_ns(td, file="d.sql"), "drop table t_old\n")
        assert code == 0 and not executed_ddl(conn, "drop")
        assert "destructive" in out and "DESTRUCTIVE" in out
        tok = last_meta(td)["token"]
        pythia.mint_grant(td, tok, "DEV")             # the developer approved it
        conn = FakeConn(base_script())
        code, _ = quiet(pythia.run_apply, conn, "APP",
                        apply_ns(td, file="d.sql", confirm=tok), "drop table t_old\n")
        assert code == 0 and executed_ddl(conn, "drop")
        assert last_meta(td)["confirmed_via"] == "grant"
        pythia.revoke_lease(td)                       # no lease: deny again
        expect_exit(lambda: quiet(pythia.run_apply, FakeConn(base_script()), "APP",
                                  apply_ns(td, file="d.sql"), "drop table t_old\n"),
                    "deny")


def test_the_lease_pauses_when_other_objects_break():
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        conn = FakeConn(base_script(invalid_after=[("X_ALREADY_BROKEN", "PROCEDURE"),
                                                   ("P_USES_ORDER", "PROCEDURE")]))
        code, out = quiet(pythia.run_apply, conn, "APP",
                          apply_ns(td, file="b.sql"), p3.NEW_FILE)
        assert code == 3
        assert pythia.lease_status(pythia.read_lease(td)) == "paused"
        assert "Autopilot stopped itself" in out
        conn2 = FakeConn(base_script())
        _, out = quiet(pythia.run_apply, conn2, "APP",
                       apply_ns(td, file="b.sql"), p3.NEW_FILE)
        assert not executed_ddl(conn2, "create or replace")
        assert "paused" in out


def test_own_compile_errors_keep_the_lease_until_five_in_a_row():
    err = [("PKG_ORDER", "PACKAGE BODY", 1, 2, 3, "ERROR", "PLS-00103: x")]
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        for i in range(pythia.LEASE_FAILS_TO_PAUSE):
            assert pythia.lease_status(pythia.read_lease(td)) == "active", i
            code, _ = quiet(pythia.run_apply, FakeConn(base_script(errors=err)),
                            "APP", apply_ns(td, file="b.sql"), p3.NEW_FILE)
            assert code == 3
        assert pythia.lease_status(pythia.read_lease(td)) == "paused"


def test_the_lease_ends_by_clock_budget_and_revocation():
    with tempfile.TemporaryDirectory() as td:
        lz = lease(td, max_writes=1)
        later = datetime.datetime.fromisoformat(lz["expires_at"]) + \
            datetime.timedelta(minutes=1)
        assert pythia.lease_status(lz, now=later) == "expired"
        pythia.lease_note_write(td, True, [], "e1")
        assert pythia.lease_status(pythia.read_lease(td)) == "spent"
        lease(td)
        assert pythia.revoke_lease(td)["revoked_at"]
        assert pythia.lease_status(pythia.read_lease(td)) == "revoked"
        ok, _, why = pythia.autopilot_check(td, "DEV", "plsql_source", "x")
        assert not ok and "revoked" in why


def test_autopilot_on_needs_a_console_and_request_does_not():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / ".pythia"
        d.mkdir()
        (d / "connections.json").write_text(json.dumps(
            {"DEV": {"user": "app", "password": "x", "dsn": "h/s"}}),
            encoding="utf-8")
        old = os.getcwd()
        os.chdir(td)
        try:
            ns = argparse.Namespace(action="on", conn=None, groups=None,
                                    hours=None, max_writes=None,
                                    destructive=False, note=None, token=None,
                                    json=True, project_root=td, color=False,
                                    console=False)
            expect_exit(lambda: quiet(pythia.cmd_autopilot, None, None, ns),
                        "console")
            assert pythia.read_lease(td) is None
            ns.action = "request"
            _, out = quiet(pythia.cmd_autopilot, None, None, ns)
            tok = json.loads(out)["token"]
            assert pythia.read_lease(td) is None             # nothing delegated
            kind, _, meta = pythia.find_approvable(td, tok)
            assert kind == "autopilot"
            ns.action, ns.console = "on", True
            _, out = quiet(pythia.cmd_autopilot, None, None, ns)
            assert json.loads(out)["lease"]["approver"] == "console"
        finally:
            os.chdir(old)


def test_every_door_can_start_a_lease_and_none_grants_more():
    with tempfile.TemporaryDirectory() as td:
        req = pythia.new_lease_request(td, "DEV", {})
        _, meta, body = pythia.approval_card(td, req["token"])
        card = f"pythia approve {req['token']}\n" + "\n".join(body)
        out = p3._run_hook(td, p3._hook_payload(card, "Approve"))
        assert "autopilot ACTIVE" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
        lz = pythia.read_lease(td)
        assert lz["approver"] == "chat" and lz["session"] == "sess-1"
        # the request is spent: approving it again has nothing to approve
        expect_exit(lambda: pythia.approval_card(td, req["token"]), "no pending")
        # and the MCP door, with an elicitation answered Approve
        req2 = pythia.new_lease_request(td, "DEV", {}, groups=["plsql_source"])
        res = pythia.mcp_approve_decision(
            td, req2["token"], lambda m, s: ("accept", {"decision": "Approve"}))
        assert res["isError"] is False
        assert pythia.read_lease(td)["approver"] == "mcp"
        assert pythia.read_lease(td)["groups"] == ["plsql_source"]


def test_autopilot_report_lists_every_write_and_its_undo():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        quiet(pythia.run_apply, FakeConn(base_script()), "APP",
              apply_ns(td, file="b.sql"), p3.NEW_FILE)
        ns = argparse.Namespace(action="report", token=None, json=True,
                                project_root=td, color=False)
        _, out = quiet(pythia.cmd_autopilot, None, None, ns)
        rep = json.loads(out)
        assert len(rep["writes"]) == 1 and rep["writes"][0]["verdict"] == "ok"
        assert "journal restore" in rep["writes"][0]["restore"]


# --- the chat hook: fail closed, and say why ------------------------------------

def test_hook_refuses_prefilled_answers():
    with tempfile.TemporaryDirectory() as td:
        card = p3._pending_preview(td)
        payload = p3._hook_payload(card, "Approve")
        payload["tool_input"]["answers"] = {card: "Approve"}
        out = p3._run_hook(td, payload)
        assert pythia.read_grant(td, "7f3a91") is None
        assert "pre-filled" in out


def test_hook_names_an_answer_that_never_arrived():
    with tempfile.TemporaryDirectory() as td:
        card = p3._pending_preview(td)
        payload = p3._hook_payload(card, "Approve")
        payload["tool_response"] = {"answers": {}}
        out = p3._run_hook(td, payload)
        assert pythia.read_grant(td, "7f3a91") is None
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "no answer came back" in ctx and "pythia_approve" in ctx
        assert "doctor" in ctx


def test_the_mcp_tool_demands_a_prompt_on_every_call():
    tool = pythia._MCP_TOOL_DEF
    assert tool["_meta"]["anthropic/requiresUserInteraction"] is True
    assert "autopilot" in tool["description"] and "deploy" in tool["description"]


# --- deploy ------------------------------------------------------------------------

def deploy_ns(td, paths, **kw):
    ns = apply_ns(td, paths=[str(p) for p in paths], command="deploy",
                  color=False, preview=False, from_n=1)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


RELEASE = """create table t_line (id number constraint pk_line primary key);
comment on table t_line is 'Order lines';
comment on column t_line.id is 'Key';
create or replace package body pkg_order as
  procedure p is begin null; end;
end pkg_order;
/
"""


def deploy_conn():
    s = base_script()
    s["from all_col_comments"] = ([("COMMENTS",)], [])
    return FakeConn(s)


def test_deploy_previews_everything_and_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        conn = deploy_conn()
        _, out = quiet(pythia.cmd_deploy, conn, "APP", deploy_ns(td, [f], json=True))
        doc = json.loads(out)
        assert doc["mode"] == "preview" and len(doc["members"]) == 4
        assert [m["group"] for m in doc["members"]] == [
            "structural", "comments", "comments", "plsql_source"]
        assert not executed_ddl(conn, "create") and not executed_ddl(conn, "comment")
        assert len(doc["batch"]) == 10
        _, _, body = pythia.approval_card(td, doc["batch"])
        assert "4 statements" in body[0]


def test_one_approval_runs_the_whole_release_in_order():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        _, out = quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                       deploy_ns(td, [f], json=True))
        batch = json.loads(out)["batch"]
        _, meta, _ = pythia.approval_card(td, batch)
        pythia.grant_approval(td, batch, meta, "console")
        conn = deploy_conn()
        _, out = quiet(pythia.cmd_deploy, conn, "APP",
                       deploy_ns(td, [f], json=True, confirm=batch))
        doc = json.loads(out)
        assert doc["ok"] and doc["stopped_at"] is None
        order = [s.lstrip().lower()[:20] for s, _ in conn.executed
                 if s.lstrip().lower().startswith(("create", "comment"))]
        assert order[0].startswith("create table") and \
            order[-1].startswith("create or replace")
        # one approval, one run
        expect_exit(lambda: quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                                  deploy_ns(td, [f], json=True, confirm=batch)),
                    "already ran")


def test_deploy_without_approval_is_refused():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        _, out = quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                       deploy_ns(td, [f], json=True))
        batch = json.loads(out)["batch"]
        conn = deploy_conn()
        expect_exit(lambda: quiet(pythia.cmd_deploy, conn, "APP",
                                  deploy_ns(td, [f], confirm=batch)),
                    "no developer approval")
        assert not executed_ddl(conn, "create")


def test_deploy_stops_at_the_first_broken_statement():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE + "create or replace procedure p_after as begin "
                     "null; end;\n/\n", encoding="utf-8")
        lease(td, groups=("plsql_source", "comments", "structural"))
        s = base_script(errors=[("PKG_ORDER", "PACKAGE BODY", 1, 2, 3, "ERROR",
                                 "PLS-00103: x")])
        s["from all_col_comments"] = ([("COMMENTS",)], [])
        conn = FakeConn(s)
        try:
            quiet(pythia.cmd_deploy, conn, "APP", deploy_ns(td, [f], json=True))
            raise AssertionError("expected exit 3")
        except SystemExit as e:
            assert e.code == 3
        assert not [x for x, _ in conn.executed if "p_after" in x.lower()]


def approved_batch(td, f, conn=None):
    _, out = quiet(pythia.cmd_deploy, conn or deploy_conn(), "APP",
                   deploy_ns(td, [f], json=True))
    batch = json.loads(out)["batch"]
    _, meta, _ = pythia.approval_card(td, batch)
    pythia.grant_approval(td, batch, meta, "console")
    return batch


def test_the_deploy_card_shows_every_statement_in_full():
    """Found in review: the card printed 70 characters of each statement —
    `grant select on t_order` while the statement went on `to public`."""
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm", data_dml="confirm", grants="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text("grant select\n  on t_order\n  to app_ro;\n"
                     "delete from t_order\n -- tidy\n where status = 'DRAFT';\n",
                     encoding="utf-8")
        s = p3.dml_script(count=2)
        _, out = quiet(pythia.cmd_deploy, FakeConn(s), "APP",
                       deploy_ns(td, [f], json=True))
        _, _, body = pythia.approval_card(td, json.loads(out)["batch"])
        text = "\n".join(body)
        assert "to app_ro" in text and "where status = 'DRAFT'" in text
        assert "2 rows in t_order" in text                # the row set itself


def test_member_approvals_live_only_inside_their_deploy():
    """Found in review: approving a batch minted ordinary grants for every
    member; after a stop they could still be used one by one."""
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE + "drop table t_old;\n", encoding="utf-8")
        batch = approved_batch(td, f)
        meta = pythia._read_json(pythia.batch_file(td, batch))
        drop = meta["members"][-1]
        assert pythia.read_grant(td, drop["token"])["batch"] == batch
        # used on its own, the member approval is refused
        conn = deploy_conn()
        expect_exit(lambda: quiet(pythia.run_apply, conn, "APP",
                                  apply_ns(td, file="x.sql", confirm=drop["token"]),
                                  "drop table t_old"), "belongs to deploy")
        assert not executed_ddl(conn, "drop")
        assert decision(pre(td, f"pythia apply x.sql --confirm {drop['token']}")) is None
        # a deploy that stops withdraws everything it did not use
        err = [("PKG_ORDER", "PACKAGE BODY", 1, 2, 3, "ERROR", "PLS-00103: x")]
        s = base_script(errors=err)
        s["from all_col_comments"] = ([("COMMENTS",)], [])
        try:
            quiet(pythia.cmd_deploy, FakeConn(s), "APP",
                  deploy_ns(td, [f], json=True, confirm=batch))
            raise AssertionError("expected exit 3")
        except SystemExit as e:
            assert e.code == 3
        assert pythia.read_grant(td, drop["token"]) is None
        assert pythia.grant_status(pythia.read_grant(td, batch), "DEV") == "spent"


def test_a_changed_script_is_caught_before_anything_runs():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        batch = approved_batch(td, f)
        f.write_text(RELEASE.replace("'Key'", "'Changed'"), encoding="utf-8")
        conn = deploy_conn()
        expect_exit(lambda: quiet(pythia.cmd_deploy, conn, "APP",
                                  deploy_ns(td, [f], confirm=batch)),
                    "changed since that preview", "statement 3", "nothing ran")
        assert not executed_ddl(conn, "create")


def test_deploy_refuses_session_changes_and_sqlplus_commands():
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "rel.sql"
        f.write_text("alter session set edition = ora$base;\n"
                     "create or replace procedure p as begin null; end;\n/\n",
                     encoding="utf-8")
        expect_exit(lambda: quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                                  deploy_ns(td, [f])), "ALTER SESSION")
        for cmd in ("exec pkg_migrate.run;", "host rm -rf /", "connect sys/x"):
            expect_exit(lambda: pythia.split_script(cmd + "\n"), "refuses")


def test_deploy_resumes_from_a_statement():
    with tempfile.TemporaryDirectory() as td:
        policy(td, structural="confirm")
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        _, out = quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                       deploy_ns(td, [f], json=True, from_n=4))
        doc = json.loads(out)
        assert [m["n"] for m in doc["members"]] == [4]
        _, meta, body = pythia.approval_card(td, doc["batch"])
        assert any("statement 4" in ln for ln in body)
        pythia.grant_approval(td, doc["batch"], meta, "console")
        expect_exit(lambda: quiet(pythia.cmd_deploy, deploy_conn(), "APP",
                                  deploy_ns(td, [f], confirm=doc["batch"])),
                    "started elsewhere")
        conn = deploy_conn()
        _, out = quiet(pythia.cmd_deploy, conn, "APP",
                       deploy_ns(td, [f], json=True, confirm=doc["batch"], from_n=4))
        assert json.loads(out)["ok"]
        assert not executed_ddl(conn, "create table")


def test_inline_plsql_in_a_query_is_not_read_only():
    assert not pythia.is_readonly_sql(
        "with function f return number is pragma autonomous_transaction; "
        "begin execute immediate 'drop table t'; return 1; end; select f from dual")
    assert pythia.is_readonly_sql("with x as (select 1 from dual) select * from x")


def test_deploy_refuses_up_front_what_the_policy_denies():
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "rel.sql"
        f.write_text(RELEASE, encoding="utf-8")
        conn = deploy_conn()
        expect_exit(lambda: quiet(pythia.cmd_deploy, conn, "APP",
                                  deploy_ns(td, [f])),
                    "before anything ran", "structural")
        assert conn.executed == []
        f.write_text("begin null; end;\n/\n", encoding="utf-8")
        expect_exit(lambda: quiet(pythia.cmd_deploy, conn, "APP",
                                  deploy_ns(td, [f])), "anonymous")


def test_a_directory_deploys_in_dependency_order_unless_numbered():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        (d / "a_body.sql").write_text("create or replace package body pkg as "
                                      "end;\n/\n", encoding="utf-8")
        (d / "b_table.sql").write_text("create table t (x number);\n",
                                       encoding="utf-8")
        (d / "c_spec.sql").write_text("create or replace package pkg as end;\n/\n",
                                      encoding="utf-8")
        files, _, _, notes = pythia.collect_deploy([d])
        assert "by what each file creates" in notes[0]
        assert [f.name for f in files] == ["b_table.sql", "c_spec.sql", "a_body.sql"]
        n = pathlib.Path(td) / "n"
        n.mkdir()
        for name in ("02_body.sql", "01_table.sql"):
            (n / name).write_text("select 1 from dual;\n", encoding="utf-8")
        files, _, _, _ = pythia.collect_deploy([n])
        assert [f.name for f in files] == ["01_table.sql", "02_body.sql"]
        # natural order: 2 before 10; Flyway-style V-numbers count as numbered;
        # .SQL counts as .sql
        f2 = pathlib.Path(td) / "f"
        f2.mkdir()
        for name in ("10_drop.sql", "2_table.sql", "1_seq.SQL"):
            (f2 / name).write_text("select 1 from dual;\n", encoding="utf-8")
        files, _, _, _ = pythia.collect_deploy([f2])
        assert [f.name for f in files] == ["1_seq.SQL", "2_table.sql", "10_drop.sql"]
        f3 = pathlib.Path(td) / "flyway"
        f3.mkdir()
        (f3 / "V3__drop_x.sql").write_text("alter table t drop column x;\n",
                                           encoding="utf-8")
        (f3 / "V2__backfill.sql").write_text("update t set y = 1 where id = 1;\n",
                                             encoding="utf-8")
        files, _, _, notes = pythia.collect_deploy([f3])
        assert [f.name for f in files] == ["V2__backfill.sql", "V3__drop_x.sql"]
        assert "numbered" in notes[0]


# --- hooks ----------------------------------------------------------------------------

def pre(td, command, mode="default", tool="Bash", tin=None):
    return pythia.pre_tool_decision({"tool_name": tool, "permission_mode": mode,
                                     "cwd": td,
                                     "tool_input": tin or {"command": command}})


def decision(out):
    return (out or {}).get("hookSpecificOutput", {}).get("permissionDecision")


def test_pre_tool_lets_reads_through_and_nothing_chained():
    with tempfile.TemporaryDirectory() as td:
        assert decision(pre(td, "pythia ls PKG_%")) == "allow"
        assert decision(pre(td, 'python -m pythia sql "select a||b from t"')) == "allow"
        # forward slashes: the Bash tool is a POSIX shell even on Windows
        # (Git Bash), where an unquoted D:\a\b path loses its backslashes
        real = pathlib.Path(pythia.__file__).resolve().as_posix()
        assert decision(pre(td, f"python3 {real} impact T_ORDER")) == "allow"
        assert decision(pre(td, "pythia apply f.sql")) == "allow"     # preview
        for cmd in ("pythia ls x && rm -rf /", "pythia ls x; rm y",
                    "pythia ls $(rm -rf ~)", "pythia ls `id`",
                    "pythia ls x | sh", "pythia policy set structural allow",
                    "pythia apply f.sql --yes", "pythia conventions --init",
                    "pythia autopilot on", "echo hi"):
            assert decision(pre(td, cmd)) is None, cmd


def test_pre_tool_cannot_be_talked_into_a_second_command():
    """Found in review: shlex treats '#' mid-word as a comment and bash does
    not, so `pythia guide#;touch x` read as one read command; argparse
    abbreviations let `--ye` and `--ho` hide --yes and --hook; option values
    passed for subcommand actions. Every one of these must be left to the
    client's own prompt — never allowed."""
    with tempfile.TemporaryDirectory() as td:
        for cmd in ("pythia guide#;touch /tmp/pwned", "pythia ls x#|sh",
                    "pythia ls x#&& rm -rf ~",
                    "pythia guide#;echo e | pythia approve --ho",
                    "pythia policy --conn show set data_dml allow",
                    "pythia journal --conn list prune",
                    "pythia apply f.sql --ye", "pythia apply f.sql --conf abc123",
                    "/tmp/x/pythia ls T", "python /tmp/elsewhere/pythia.py ls T",
                    "PYTHIA_CI=1 pythia apply f.sql --yes"):
            assert decision(pre(td, cmd)) is None, cmd
        # python -m pythia with a pythia.py in the working directory imports THAT
        (pathlib.Path(td) / "pythia.py").write_text("print('not pythia')\n",
                                                    encoding="utf-8")
        assert decision(pre(td, "python -m pythia ls T")) is None
    expect_exit(lambda: pythia.build_parser().parse_args(["apply", "f", "--ye"]))
    expect_exit(lambda: pythia.build_parser().parse_args(["approve", "--ho"]))


@contextlib.contextmanager
def _interpreter(path):
    """Run as if sys.executable were `path` — the hook's own interpreter."""
    old = sys.executable
    sys.executable = str(path)
    try:
        yield
    finally:
        sys.executable = old


def test_pre_tool_lets_reads_through_this_interpreter_by_absolute_path():
    """Field test 0.15.4: pythia in a venv that is not on PATH. The commands
    that run there are spelled with the venv's interpreter by absolute path —
    the same launcher the hooks use — and every read was a prompt, because
    the guard knew only `pythia` and `python -m pythia`."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as v:
        exe = pathlib.Path(v) / "bin" / ("python.exe" if os.name == "nt" else "python3")
        script = exe.with_name("pythia.exe" if os.name == "nt" else "pythia")
        with _interpreter(exe):
            q = exe.as_posix()
            assert decision(pre(td, f'"{q}" -m pythia src PKG_ORDER')) == "allow"
            assert decision(pre(td, f"{q} -m pythia connections")) == "allow"
            assert decision(pre(td, f'"{q}" -m pythia apply f.sql')) == "allow"   # preview
            # the install's own entry point, beside its interpreter
            assert decision(pre(td, f'"{script.as_posix()}" src PKG_ORDER')) == "allow"
            # a clone's launcher: this interpreter and this very pythia.py
            real = pathlib.Path(pythia.__file__).resolve().as_posix()
            assert decision(pre(td, f'"{q}" "{real}" src PKG_ORDER')) == "allow"


def test_pre_tool_trusts_no_other_absolute_launcher():
    """Allowing a command runs it without asking. An absolute path is
    trusted only when it is, by spelling, the interpreter running this hook
    (or its own pythia script): not a look-alike, not a symlink to it — a
    venv's python IS a symlink, and only its own path loads its packages."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as v:
        exe = pathlib.Path(v) / "bin" / "python3"
        with _interpreter(exe):
            q = exe.as_posix()
            for cmd in (f'"{pathlib.Path(v).as_posix()}/evil/python3" -m pythia src X',
                        f'"{q}x" -m pythia src X',
                        f'"{exe.with_name("python4").as_posix()}" -m pythia src X',
                        f'"{exe.with_name("evil").as_posix()}" src X',
                        "./bin/python3 -m pythia src X",           # relative: cwd decides
                        f'"{q}" -c "import os" -m pythia src X',
                        f'"{q}" -m pythia src X; rm -rf ~',
                        f'"{q}" -m pythia src X && touch y',
                        f'"{q}" -m pythia apply f.sql --yes',
                        f'"{q}" -m pythia policy set structural allow',
                        f'"{q}" -m pythiax src X',
                        f'"{q}" /tmp/elsewhere/pythia.py src X'):
                assert decision(pre(td, cmd)) is None, cmd
            # a pythia.py in the working directory shadows `-m pythia`
            (pathlib.Path(td) / "pythia.py").write_text("print('x')\n", encoding="utf-8")
            assert decision(pre(td, f'"{q}" -m pythia src X')) is None
        # the same path while ANOTHER interpreter runs the hook: not trusted
        with _interpreter(pathlib.Path(v) / "other" / "python3"), \
                tempfile.TemporaryDirectory() as clean:
            assert decision(pre(clean, f'"{exe.as_posix()}" -m pythia src X')) is None
        # a symlink to the real interpreter is another spelling: not trusted
        link = pathlib.Path(v) / "link-python3"
        try:
            os.symlink(sys.executable, link)
        except (OSError, NotImplementedError):
            return                       # no symlinks here (Windows without rights)
        with tempfile.TemporaryDirectory() as clean:
            assert decision(pre(clean, f'"{link.as_posix()}" -m pythia src X')) is None


@contextlib.contextmanager
def _path_finds(found):
    """path_lookup answering `found(name)` instead of reading PATH."""
    old = pythia.path_lookup
    pythia.path_lookup = lambda name, *a, **k: found(name)
    try:
        yield
    finally:
        pythia.path_lookup = old


def test_printed_commands_run_where_pythia_is_not_on_path():
    """Field test 0.15.4: a real Claude Code session in a project whose
    pythia lives in a venv that is not on PATH ran `pythia ...` and got
    `command not found`; the install's fallback said `python3 -m pythia`,
    which on that machine is the system interpreter, without pythia. A
    printed command names what runs here: the bare name only when PATH finds
    this very file, else this interpreter by absolute path."""
    with tempfile.TemporaryDirectory() as v:
        exe = pathlib.Path(v) / "bin" / "python3"
        script = exe.with_name("pythia")
        launcher = f'"{exe.as_posix()}" -m pythia'
        old = sys.argv[0]
        try:
            with _interpreter(exe):
                sys.argv[0] = str(script)                      # console script
                with _path_finds(lambda n: "/usr/bin/" + n):   # PATH: other ones
                    assert pythia.invocation() == launcher
                    hint = pythia.entry_point_hint(None, str(exe.parent))
                    assert launcher in hint and "\n  python3 -m pythia" not in hint
                with _path_finds(lambda n: str(script) if n == "pythia" else None):
                    assert pythia.invocation() == "pythia"
                sys.argv[0] = "scripts/pythia.py"              # from a clone
                with _path_finds(lambda n: str(exe) if n == "python3" else None):
                    assert pythia.invocation() == "python3 scripts/pythia.py"
                with _path_finds(lambda n: None):
                    assert pythia.invocation() == f'"{exe.as_posix()}" scripts/pythia.py'
        finally:
            sys.argv[0] = old


def _session_start(td, home=None):
    import argparse
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        _, out = quiet(pythia.cmd_hook, None, None,
                       argparse.Namespace(event="session-start", project_root=td))
    finally:
        sys.stdin = old_stdin
    return out


def test_session_start_names_the_launcher_that_runs_here():
    """CLAUDE.md is shared, so it says `pythia`. The hook runs on this
    machine: it says how `pythia` is spelled here, once, and spells its own
    pointers that way."""
    launcher = '"/v/bin/python3" -m pythia'
    with tempfile.TemporaryDirectory() as td:
        pythia.scaffold_config(td)
        pythia.merge_marked_block(pathlib.Path(td) / "CLAUDE.md",
                                  pythia.claude_md_block())
        old = pythia.invocation
        try:
            pythia.invocation = lambda: launcher
            out = _session_start(td)
            assert f"`pythia` here is `{launcher}`" in out, out
            assert f"`{launcher} guide`" in out and f"`{launcher} connections`" in out
            # field test 0.15.5: the agent put the long launcher in a shell
            # variable (PY=...; "$PY" -m pythia ...) — a compound command, so
            # the guard asked instead of allowing. The line says how to type it.
            run = next(ln for ln in out.splitlines() if ln.startswith("RUN"))
            assert "as written" in run and "one command per call" in run, run
            assert "variable" in run and "`;`" in run, run
            pythia.invocation = lambda: "pythia"
            out = _session_start(td)
            assert "`pythia` here is" not in out and "`pythia guide`" in out
        finally:
            pythia.invocation = old


def test_session_state_names_a_local_scope_approval_door():
    """Field test 0.15.4: in a venv project the MCP approver lives in Claude
    Code's local scope (~/.claude.json), not .mcp.json — and the session
    state said nothing about the door that prompts in every mode."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        assert not any("approval door" in ln
                       for ln in pythia.session_state_lines(td, home=home))
        (pathlib.Path(home) / ".claude.json").write_text(json.dumps({"projects": {
            str(pathlib.Path(td)): {"mcpServers": {"pythia": {
                "command": "/v/bin/python3", "args": ["-m", "pythia", "mcp"]}}}}}),
            encoding="utf-8")
        assert any("approval door" in ln and "pythia_approve" in ln
                   for ln in pythia.session_state_lines(td, home=home))


def test_tokens_that_are_not_tokens_name_nothing():
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        assert pythia.read_grant(td, "../autopilot/lease") is None
        assert pythia.find_approvable(td, "") == (None, None, None)
        pythia.write_journal_entry(td, "PROCEDURE", "P", "x", "x",
                                   {"snapshot": True})       # no token key
        expect_exit(lambda: pythia.approval_card(td, ""), "no pending")


def test_pre_tool_denies_writes_around_the_gate():
    with tempfile.TemporaryDirectory() as td:
        for cmd in ("sqlplus app/x@db <<EOF\ncreate or replace procedure p as "
                    "begin null; end;\n/\nEOF",
                    "echo 'drop table t;' | sqlplus -s app/x@db",
                    "sql app/x@db @release.sql",
                    "pythia approve --hook < fake.json"):
            assert decision(pre(td, cmd)) == "deny", cmd
        assert decision(pre(td, "sqlplus -v")) is None
        assert decision(pre(td, "echo 'select 1 from dual;' | sqlplus -s a/b@c")) is None
        assert decision(pre(td, "", tool="mcp__sqlcl__run-sql",
                            tin={"sql": "CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;"})) == "deny"
        assert decision(pre(td, "", tool="mcp__sqlcl__run-sql",
                            tin={"sql": "select * from t_order"})) is None


def test_pre_tool_passes_approved_writes_only():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / ".pythia"
        d.mkdir()
        (d / "connections.json").write_text(json.dumps({"DEV": {"user": "a"}}),
                                            encoding="utf-8")
        cmd = "pythia apply f.sql --confirm 7f3a91"
        assert decision(pre(td, cmd)) is None                # client decides
        pythia.mint_grant(td, "7f3a91", "DEV")
        assert decision(pre(td, cmd)) == "allow"
        assert decision(pre(td, cmd + " --conn TEST")) is None   # other conn
        assert decision(pre(td, cmd, mode="plan")) is None
        pythia.spend_grant(td, "7f3a91")
        assert decision(pre(td, cmd)) is None
        lease(td)
        assert decision(pre(td, "pythia apply f.sql --confirm 999999")) == "allow"
        (d / "settings.json").write_text(json.dumps({"auto_allow_reads": False}),
                                         encoding="utf-8")
        assert decision(pre(td, "pythia ls x")) is None      # the developer opted out


def test_post_tool_puts_the_verdict_next_to_the_result():
    def note(cmd, stdout):
        out = pythia.post_tool_note({"tool_name": "Bash",
                                     "tool_input": {"command": cmd},
                                     "tool_response": {"stdout": stdout}})
        return (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "EXIT 3" in note("pythia apply f.sql --confirm abc123",
                            "Applied P (PROCEDURE) — but it did not compile cleanly:")
    assert "pipe" in note("pythia apply f.sql | tail -5", "anything")
    assert "Refusal" in note("pythia apply f.sql", "Refused: structural is set to deny") \
        or "refusal" in note("pythia apply f.sql", "Refused: structural is set to deny")
    assert note("pythia ls x", "did not compile cleanly") == ""
    assert note("pythia apply f.sql", "Applied P. Compiled clean.") == ""


def test_session_start_prints_the_guide_and_the_live_state():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        lease(td)
        d = pathlib.Path(td) / ".pythia"
        (d / "conventions.md").write_text("# rules", encoding="utf-8")
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            _, out = quiet(pythia.cmd_hook, None, None,
                           argparse.Namespace(event="session-start",
                                              project_root=td))
        finally:
            sys.stdin = old_stdin
        assert "PYTHIA HARNESS ACTIVE" in out
        assert "autopilot: ACTIVE on DEV" in out
        assert "conventions.md" in out


def test_hooks_fail_silent_on_garbage():
    import argparse
    old_stdin = sys.stdin
    try:
        for event in ("pre-tool", "post-tool"):
            sys.stdin = io.StringIO("{not json")
            _, out = quiet(pythia.cmd_hook, None, None,
                           argparse.Namespace(event=event, project_root="."))
            assert out == ""
    finally:
        sys.stdin = old_stdin


def test_no_pythia_pretool_matcher_can_catch_askuserquestion():
    for entry in pythia.CLAUDE_HOOKS["PreToolUse"]:
        assert not pythia._matcher_hits(entry["matcher"], "AskUserQuestion")
    hooks = json.loads((HERE.parent / "hooks" / "hooks.json").read_text(
        encoding="utf-8"))["hooks"]
    for entry in hooks["PreToolUse"]:
        assert not pythia._matcher_hits(entry["matcher"], "AskUserQuestion")


# --- doctor -------------------------------------------------------------------------------

def test_doctor_finds_what_breaks_askuserquestion():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        cl = pathlib.Path(td) / ".claude"
        cl.mkdir()
        (cl / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["AskUserQuestion"]},
            "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "log-everything"}]},
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "python -m pythia hook pre-tool"}]}]}}),
            encoding="utf-8")
        sk = pathlib.Path(home) / ".claude" / "skills" / "eager"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("---\nname: eager\ndescription: Use when x\n"
                                     "allowed-tools: Bash, AskUserQuestion\n---\n",
                                     encoding="utf-8")
        (pathlib.Path(home) / ".claude" / "settings.json").write_text(
            json.dumps({"permissions": {"defaultMode": "dontAsk"}}), encoding="utf-8")
        found = [w for _, w in pythia.ask_question_breakers(td, home)]
        assert any("permissions.allow" in w for w in found)
        assert any("'*'" in w for w in found)
        assert not any("hook pre-tool" in w for w in found)   # ours is Bash-only
        assert any("allowed-tools" in w for w in found)
        assert any("dontAsk" in w for w in found)


def test_doctor_reports_the_wiring_after_install():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_mcp(td, launcher=_good_launcher())
        pythia.install_claude_hooks(td, trust_mcp=True, launcher=_good_launcher())
        rows = pythia.harness_checks(td, home)
        msgs = {(s, a): m for s, a, m in rows}
        assert all(s != "fail" for s, _, _ in rows), rows
        hooks = [m for (s, a), m in msgs.items() if a == "claude hooks"]
        assert all("wired" in m for m in hooks) and len(hooks) >= 1
        # registered; the launcher is this machine's path in the shared
        # .mcp.json, which doctor names (warn) but never runs
        assert any(a == "approval door" and s == "warn" and "registered" in m
                   and "shares" in m for s, a, m in rows), rows


def _cred(rows):
    found = [(s, m) for s, a, m in rows if a == "credentials"]
    assert len(found) == 1, rows
    return found[0]


def test_doctor_fails_a_password_file_git_tracks():
    """Not ignored is a risk (warn); tracked is a leak (fail) — the passwords
    are in the repository already, and a .gitignore line cannot take them
    back out. The fix names the commands and the rotation."""
    import subprocess
    with ti.hermetic_git(), tempfile.TemporaryDirectory() as td, \
            tempfile.TemporaryDirectory() as home:
        git = ti.git_init(td)
        if git is None:
            return
        root = pathlib.Path(td)
        pythia.scaffold_config(root)
        st, msg = _cred(pythia.harness_checks(td, home))
        assert st == "warn" and "git add -A" in msg and "install" in msg, msg
        pythia.ignore_connections(root)
        assert _cred(pythia.harness_checks(td, home))[0] == "ok"
        subprocess.run([git, "-C", td, "add", "-f", ti.SECRET], check=True,
                       stdin=subprocess.DEVNULL)
        st, msg = _cred(pythia.harness_checks(td, home))
        assert st == "fail", msg
        assert f"git rm --cached {ti.SECRET}" in msg and "password" in msg, msg


def test_doctor_warns_a_rule_only_this_machine_has():
    with ti.hermetic_git(), tempfile.TemporaryDirectory() as td, \
            tempfile.TemporaryDirectory() as home:
        if ti.git_init(td) is None:
            return
        root = pathlib.Path(td)
        pythia.scaffold_config(root)
        exclude = root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text(ti.SECRET + "\n", encoding="utf-8")
        st, msg = _cred(pythia.harness_checks(td, home))
        assert st == "warn" and "only on this machine" in msg, msg


def test_doctor_says_plainly_when_there_is_no_git_to_ask():
    with ti.hermetic_git(), tempfile.TemporaryDirectory() as td, \
            tempfile.TemporaryDirectory() as home:
        pythia.scaffold_config(pathlib.Path(td))
        st, msg = _cred(pythia.harness_checks(td, home))
        assert st == "info" and "git" in msg, msg
        # no password file, nothing to protect: no row at all
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        assert not [a for _, a, _ in pythia.harness_checks(td, home)
                    if a == "credentials"]


def test_install_registers_the_mcp_approver_once_and_keeps_other_servers():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / ".mcp.json"
        path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}),
                        encoding="utf-8")
        assert pythia.install_claude_mcp(td) == (path, "added")
        s = json.loads(path.read_text(encoding="utf-8"))
        assert s["mcpServers"]["other"] == {"command": "x"}
        assert s["mcpServers"]["pythia"]["args"] == ["-m", "pythia", "mcp"]
        assert pythia.install_claude_mcp(td) == (path, "present")
        path.write_text("{broken", encoding="utf-8")
        assert pythia.install_claude_mcp(td) == (path, None)
        assert path.read_text(encoding="utf-8") == "{broken"


# --- explain, skill ---------------------------------------------------------------------------

def test_explain_parses_never_runs_and_rolls_back():
    import argparse
    plan = ["Plan hash value: 1",
            "| Id | Operation          | Name    |",
            "|  0 | SELECT STATEMENT   |         |",
            "|  1 |  TABLE ACCESS FULL | T_ORDER |",
            "   - dynamic statistics used: dynamic sampling (level=2)"]
    conn = FakeConn({"dbms_xplan.display": ([("PLAN_TABLE_OUTPUT",)],
                                            [(ln,) for ln in plan])})
    ns = argparse.Namespace(statement=["select * from t_order where id = 1"],
                            file=None, format="TYPICAL", json=True)
    _, out = quiet(pythia.cmd_explain, conn, "APP", ns)
    doc = json.loads(out)
    assert doc["plan"] == plan
    assert any("T_ORDER" in n for n in doc["notes"])
    assert any("statistics" in n for n in doc["notes"])
    assert conn.executed[0][0].startswith("EXPLAIN PLAN SET STATEMENT_ID")
    assert conn.rollbacks == 1
    ns.statement = ["delete from t_order"]
    expect_exit(lambda: pythia.cmd_explain(FakeConn({}), "APP", ns), "SELECT")


def test_skill_new_scaffolds_what_skill_lint_accepts_once_filled():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        ns = argparse.Namespace(action="new", name="team-release-notes",
                                paths=[], dir=None,
                                trigger=["writing", "release", "notes"],
                                project_root=td, json=False)
        quiet(pythia.cmd_skill, None, None, ns)
        path = pathlib.Path(td) / ".claude" / "skills" / "team-release-notes" / "SKILL.md"
        assert path.is_file()
        assert pythia.skill_lint_findings(path) == []
        path.write_text("---\nname: wrong\ndescription: does things: well\n---\n",
                        encoding="utf-8")
        probs = " ".join(pythia.skill_lint_findings(path))
        assert "folder" in probs and "Use when" in probs and "': '" in probs
        expect_exit(lambda: quiet(pythia.cmd_skill, None, None, ns), "exists")


def test_new_commands_are_in_the_parser_and_the_guide():
    p = pythia.build_parser()
    for argv in (["autopilot", "request", "--groups", "all", "--hours", "4"],
                 ["deploy", "a.sql", "b.sql"], ["lint", "x.sql"],
                 ["explain", "select 1 from dual"], ["stats", "T"],
                 ["comments", "T", "--scaffold"], ["doctor", "--db"],
                 ["hook", "pre-tool"], ["skill", "lint"],
                 ["apply", "f.sql", "--preview"]):
        p.parse_args(argv)
    for cmd in ("autopilot", "deploy", "lint", "explain", "stats", "comments",
                "doctor", "hook", "skill"):
        assert cmd in pythia.COMMANDS and cmd in pythia.OPERATING_GUIDE


# --- launcher: the interpreter the hooks and the MCP approver start -------------
#
# Field test 0.15.1: pythia installed in a venv, Claude Code started where
# `python` was the system interpreter. `python -m pythia hook pre-tool` died
# with "No module named pythia" — a non-blocking hook error, so the sqlplus
# guard let the write through; the MCP approver never connected; doctor said
# ok. The launcher is now chosen by what really starts pythia, and checked.

def _launch(**kw):
    base = dict(venv=False, which=lambda n: f"/usr/bin/{n}",
                executable="/usr/bin/python3", script="/s/pythia.py")
    base.update(kw)
    return pythia.choose_launcher(**base)


def test_launcher_is_portable_only_when_that_spelling_really_starts_pythia():
    assert _launch(starts=lambda l: True)[:2] == ("python -m pythia", True)
    # `python` is on PATH but it is another interpreter, without pythia
    assert _launch(starts=lambda l: l == "python3 -m pythia")[:2] == (
        "python3 -m pythia", True)
    # no bare name imports it: the absolute interpreter, never shared
    l, portable, why = _launch(starts=lambda l: l.startswith('"'))
    assert (l, portable) == ('"/usr/bin/python3" -m pythia', False) and why
    # not importable at all — a clone run as scripts/pythia.py: the script
    l, portable, _ = _launch(starts=lambda l: l.endswith('pythia.py"'))
    assert (l, portable) == ('"/usr/bin/python3" "/s/pythia.py"', False)
    # a name that is not on PATH is never probed, let alone chosen
    l, portable, _ = _launch(which=lambda n: None, starts=lambda l: True)
    assert (l, portable) == ('"/usr/bin/python3" -m pythia', False)


def test_launcher_in_a_virtualenv_is_absolute_even_when_python_finds_it_now():
    """`python -m pythia` works in the activated venv the developer installs
    from, and not in the desktop app or IDE that starts Claude Code later."""
    l, portable, why = _launch(venv=True, which=lambda n: f"/v/bin/{n}",
                               executable="/v/bin/python",
                               starts=lambda l: True)
    assert (l, portable) == ('"/v/bin/python" -m pythia', False)
    assert "virtual environment" in why


def test_absolute_launcher_goes_to_settings_local_never_the_shared_file():
    launcher = '"/v/bin/python" -m pythia'
    with tempfile.TemporaryDirectory() as td:
        local = pathlib.Path(td) / ".claude" / "settings.local.json"
        local.parent.mkdir()
        local.write_text(json.dumps({"env": {"A": "1"},
                                     "permissions": {"allow": ["Bash(ls)"]}}),
                         encoding="utf-8")
        path, added = pythia.install_claude_hooks(td, launcher=launcher,
                                                  local=True)
        assert path == local and added
        assert not (pathlib.Path(td) / ".claude" / "settings.json").exists()
        s = json.loads(local.read_text(encoding="utf-8"))
        assert s["env"] == {"A": "1"}                       # theirs, kept
        assert s["permissions"]["allow"] == ["Bash(ls)"]
        cmds = [h["command"] for ev in s["hooks"].values()
                for e in ev for h in e["hooks"]]
        assert cmds and all(c.startswith(launcher + " ") for c in cmds), cmds
        assert {pythia._hook_mark(c) for c in cmds} == {
            "hook session-start", "hook pre-tool", "approve --hook",
            "hook post-tool"}
        assert f"Bash({launcher} approve --hook*)" in s["permissions"]["deny"]
        assert set(pythia.CLAUDE_DENY) <= set(s["permissions"]["deny"])
        assert "enabledMcpjsonServers" not in s    # a local server needs no trust
        assert pythia.install_claude_hooks(td, launcher=launcher,
                                           local=True) == (path, [])


def test_a_moved_interpreter_is_respelled_in_this_machines_files_only():
    """A venv recreated elsewhere leaves the old path in the hooks: present by
    what they run, dead by how they start it. In this machine's own files the
    pythia entries are respelled; in the shared file they are the team's."""
    old, new = '"/old/bin/python" -m pythia', '"/new/bin/python" -m pythia'
    with tempfile.TemporaryDirectory() as td:
        pythia.install_claude_hooks(td, launcher=old, local=True)
        path, added = pythia.install_claude_hooks(td, launcher=new, local=True)
        assert added
        s = json.loads(path.read_text(encoding="utf-8"))
        cmds = [h["command"] for ev in s["hooks"].values()
                for e in ev for h in e["hooks"]]
        assert cmds and all(c.startswith(new + " ") for c in cmds), cmds
        assert len(cmds) == 5                                   # not doubled
    with tempfile.TemporaryDirectory() as td:                   # shared: kept
        pythia.install_claude_hooks(td, launcher="python3 -m pythia")
        path, _ = pythia.install_claude_hooks(td, launcher="python -m pythia")
        cmds = json.dumps(json.loads(path.read_text(encoding="utf-8"))["hooks"])
        assert "python3 -m pythia" in cmds and '"python -m pythia' not in cmds


def test_respell_never_touches_another_tools_hook():
    """`hook post-tool` is two words any tool may use. Only a command started
    by a pythia launcher is pythia's to respell (or to count as present)."""
    launcher = '"/v/bin/python" -m pythia'
    theirs = "~/bin/audit.sh hook post-tool"
    with tempfile.TemporaryDirectory() as td:
        local = pathlib.Path(td) / ".claude" / "settings.local.json"
        local.parent.mkdir()
        local.write_text(json.dumps({"hooks": {"PostToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": theirs}]}]}}),
            encoding="utf-8")
        pythia.install_claude_hooks(td, launcher=launcher, local=True)
        s = json.loads(local.read_text(encoding="utf-8"))
        cmds = [h["command"] for ev in s["hooks"].values()
                for e in ev for h in e["hooks"]]
        assert theirs in cmds                                    # untouched
        assert f"{launcher} hook post-tool" in cmds              # ours added


def test_a_bare_launcher_is_found_on_path_only():
    """Windows runs a bare name from the interpreter's own directory or the
    current directory before PATH — so a bare name is resolved by walking
    PATH alone (relative entries skipped) and that path is what runs."""
    exe = "python.exe" if os.name == "nt" else "python"
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as cwd:
        for d in (d1, cwd):
            f = pathlib.Path(d) / exe
            f.write_text("", encoding="utf-8")
            f.chmod(0o755)
        here = os.getcwd()
        os.chdir(cwd)
        try:
            assert pythia.path_lookup("python", path=os.pathsep.join(["", ".", "x"])) is None
            found = pythia.path_lookup("python", path=os.pathsep.join([".", d1]))
            assert found and pathlib.Path(found).parent == pathlib.Path(d1)
        finally:
            os.chdir(here)
    ok, why = pythia.launcher_starts("no-such-python-xyz -m pythia")
    assert not ok and "PATH" in why


def test_conda_counts_as_an_environment_that_must_be_active():
    assert pythia.in_virtualenv(prefix="/c", base="/c", env={"CONDA_PREFIX": "/c"})
    with tempfile.TemporaryDirectory() as p:
        (pathlib.Path(p) / "conda-meta").mkdir()
        assert pythia.in_virtualenv(prefix=p, base=p, env={})
    assert not pythia.in_virtualenv(prefix="/u", base="/u", env={})
    ok = lambda l, cwd=None: (True, "")                       # noqa: E731
    with tempfile.TemporaryDirectory() as env:
        st, msg = pythia.launcher_health(
            "python -m pythia", which=lambda n: f"{env}/bin/{n}",
            env={"CONDA_PREFIX": env}, starts=ok)
        assert st == "warn" and "environment" in msg


def test_a_tracked_settings_local_is_a_shared_file():
    """A repository can commit .claude/settings.local.json. Then it is
    repository content: doctor names its paths without running them, and
    install does not write this machine's interpreter into it."""
    import shutil
    import subprocess
    if not shutil.which("git"):
        return
    launcher = '"/v/bin/python" -m pythia'
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        subprocess.run(["git", "init", "-q", td], check=True)
        local = pathlib.Path(td) / ".claude" / "settings.local.json"
        local.parent.mkdir()
        local.write_text(json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command":
                '"/nonexistent/python" -m pythia hook pre-tool'}]}]}}),
            encoding="utf-8")
        subprocess.run(["git", "-C", td, "add", "-f", str(local)], check=True)
        assert pythia.git_tracks(td, ".claude/settings.local.json")
        rows = pythia.harness_checks(td, home)
        assert any(s == "warn" and a == "hook launch" and "shares" in m
                   for s, a, m in rows), rows
        before = local.read_text(encoding="utf-8")
        old = pythia.register_local_mcp
        pythia.register_local_mcp = lambda *a, **kw: ("present", "")
        try:
            _, out = quiet(pythia.report_local_launcher, td, launcher, "why")
        finally:
            pythia.register_local_mcp = old
        assert local.read_text(encoding="utf-8") == before
        assert "tracked" in out


def test_plain_launcher_refuses_relative_paths():
    for text in ("tools/python -m pythia", 'python "tools/pythia.py"',
                 "./python -m pythia", r"tools\python.exe -m pythia"):
        assert pythia.plain_launcher(text) is None, text
    assert pythia.plain_launcher('"/abs/python" -m pythia')
    assert pythia.plain_launcher('"C:/Py/python.exe" "C:/k/scripts/pythia.py"')
    assert pythia.plain_launcher("python -m pythia")


def test_a_shared_path_is_named_before_anything_is_resolved():
    for text in ('"/abs/python" -m pythia', 'python3.13 "/s/pythia.py"'):
        st, msg = pythia.launcher_health(text, shared=True, which=lambda n: None)
        assert st == "warn" and "shares" in msg, (text, st, msg)


def test_the_exclude_file_is_found_in_worktrees_and_survives_odd_bytes():
    with tempfile.TemporaryDirectory() as td:
        common = pathlib.Path(td) / "main.git"
        wt_git = common / "worktrees" / "wt"
        wt_git.mkdir(parents=True)
        (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
        (common / "info").mkdir()
        (common / "info" / "exclude").write_bytes(b"# \xff\xfe odd\n")
        work = pathlib.Path(td) / "wt"
        work.mkdir()
        (work / ".git").write_text(f"gitdir: {wt_git.as_posix()}\n", encoding="utf-8")
        assert pythia.exclude_local_settings(work) == common / "info" / "exclude"
        body = (common / "info" / "exclude").read_bytes()
        assert body.startswith(b"# \xff\xfe odd\n")              # theirs, intact
        assert body.count(b".claude/settings.local.json") == 1
        assert pythia.exclude_local_settings(work) is None       # once
        sub = pathlib.Path(td) / "sub"                           # a submodule
        sub_git = pathlib.Path(td) / "modules" / "sub"
        sub_git.mkdir(parents=True)
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ../modules/sub\n", encoding="utf-8")
        assert pythia.exclude_local_settings(sub) == sub_git / "info" / "exclude"


def test_portable_launcher_spells_every_shared_entry_the_same_way():
    with tempfile.TemporaryDirectory() as td:
        pythia.install_claude_hooks(td, launcher="python3 -m pythia")
        s = json.loads((pathlib.Path(td) / ".claude" / "settings.json")
                       .read_text(encoding="utf-8"))
        cmds = [h["command"] for ev in s["hooks"].values()
                for e in ev for h in e["hooks"]]
        assert cmds and all(c.startswith("python3 -m pythia ") for c in cmds)
        pythia.install_claude_mcp(td, launcher="python3 -m pythia")
        m = json.loads((pathlib.Path(td) / ".mcp.json").read_text(encoding="utf-8"))
        assert m["mcpServers"]["pythia"] == {"command": "python3",
                                             "args": ["-m", "pythia", "mcp"]}


class _Ran:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def test_local_mcp_is_registered_through_the_claude_cli_only():
    """A machine-specific MCP command must not land in the shared .mcp.json.
    Claude Code's local scope holds it; `claude mcp add --scope local` is the
    supported way in, and a local entry overrides the project's."""
    launcher = '"/v/bin/python" -m pythia'
    want = ["/v/bin/python", "-m", "pythia", "mcp"]
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw.get("cwd")))
        return _Ran(0)

    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        claude = lambda n: "/x/claude" if n == "claude" else None   # noqa: E731
        action, shown = pythia.register_local_mcp(td, launcher, which=claude,
                                                  run=run, home=home)
        assert action == "added"
        assert calls == [(["/x/claude", "mcp", "add", "--scope", "local",
                           "pythia", "--", *want], str(td))]
        assert not (pathlib.Path(td) / ".mcp.json").exists()

        def state(cmd):
            key = str(pathlib.Path(td).resolve())
            (pathlib.Path(home) / ".claude.json").write_text(json.dumps(
                {"projects": {key: {"mcpServers": {"pythia": {
                    "type": "stdio", "command": cmd,
                    "args": ["-m", "pythia", "mcp"]}}}}}), encoding="utf-8")

        state("/v/bin/python")                       # already there, same
        calls.clear()
        assert pythia.register_local_mcp(td, launcher, which=claude, run=run,
                                         home=home)[0] == "present"
        assert calls == []
        state("/old/venv/bin/python")                # stale: venv moved
        assert pythia.register_local_mcp(td, launcher, which=claude, run=run,
                                         home=home)[0] == "updated"
        assert [c[0][:4] for c in calls] == [
            ["/x/claude", "mcp", "remove", "--scope"],
            ["/x/claude", "mcp", "add", "--scope"]]
        # the new add fails: the old entry is put back, not lost
        calls.clear()
        def add_fails(argv, **kw):
            calls.append((argv, kw.get("cwd")))
            return _Ran(1 if argv[2] == "add" and "/v/bin/python" in argv else 0)
        assert pythia.register_local_mcp(td, launcher, which=claude,
                                         run=add_fails, home=home)[0] == "failed"
        assert calls[-1][0][2] == "add" and "/old/venv/bin/python" in calls[-1][0]
        calls.clear()
        action, shown = pythia.register_local_mcp(
            td, launcher, which=lambda n: None, run=run, home=home)
        assert action == "manual" and calls == []
        assert ("claude mcp add --scope local pythia -- /v/bin/python -m "
                "pythia mcp") in shown
        failing = lambda argv, **kw: _Ran(1, "boom")          # noqa: E731
        os.remove(pathlib.Path(home) / ".claude.json")
        assert pythia.register_local_mcp(td, launcher, which=claude,
                                         run=failing, home=home)[0] == "failed"


def _good_launcher():
    """A launcher that starts pythia in any test run: this interpreter and
    this very script (CI does not pip-install the package)."""
    exe = pathlib.Path(sys.executable).as_posix()
    return f'"{exe}" "{pathlib.Path(pythia.__file__).resolve().as_posix()}"'


def test_doctor_fails_a_launcher_that_cannot_start_pythia():
    bad = '"/nonexistent/bin/python" -m pythia'
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_hooks(td, launcher=bad, local=True)
        rows = pythia.harness_checks(td, home)
        fails = [m for s, a, m in rows if s == "fail" and a == "hook launch"]
        assert fails and "/nonexistent/bin/python" in fails[0], rows
        # the hooks are found in settings.local.json: wired, but cannot start
        assert all("wired" in m for s, a, m in rows if a == "claude hooks"), rows
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_hooks(td, launcher=_good_launcher(), local=True)
        rows = pythia.harness_checks(td, home)
        launch = [(s, m) for s, a, m in rows if a == "hook launch"]
        assert launch and all(s == "ok" for s, _ in launch), launch


def test_doctor_checks_the_local_mcp_entry_too():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        key = str(pathlib.Path(td).resolve())
        (pathlib.Path(home) / ".claude.json").write_text(json.dumps(
            {"projects": {key: {"mcpServers": {"pythia": {
                "type": "stdio", "command": "/nonexistent/bin/python",
                "args": ["-m", "pythia", "mcp"]}}}}}), encoding="utf-8")
        rows = pythia.harness_checks(td, home)
        assert any(s == "fail" and a == "approval door" and "local" in m
                   for s, a, m in rows), rows


def test_doctor_never_runs_a_hook_command_it_did_not_recognise():
    """settings.json is repository content. doctor starts a hook's interpreter
    to prove it imports pythia only when the command is exactly an interpreter
    plus `-m pythia` or a pythia.py path — and never through a shell."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        canary = pathlib.Path(td) / "pwned"
        tricks = [f"touch {canary.as_posix()} && python -m pythia hook pre-tool",
                  f"python -c 'open(\"{canary.as_posix()}\",\"w\")' -m pythia hook pre-tool",
                  f"$(touch {canary.as_posix()}) -m pythia hook post-tool",
                  f"sh {canary.as_posix()}.sh -m pythia approve --hook"]
        path = pathlib.Path(td) / ".claude" / "settings.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": c}]}
            for c in tricks]}}), encoding="utf-8")
        rows = pythia.harness_checks(td, home)
        assert not canary.exists()
        odd = [m for s, a, m in rows if a == "hook launch" and s == "warn"]
        assert len(odd) == len(tricks) and all("not a plain" in m for m in odd), rows


def test_doctor_never_runs_a_script_path_from_a_shared_file():
    """A bare interpreter is the developer's; the pythia.py it is pointed at
    by a shared file is the repository's. Only `<bare> -m pythia` is run from
    a shared file."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        canary = pathlib.Path(td) / "pwned"
        evil = pathlib.Path(td) / "tools" / "pythia.py"
        evil.parent.mkdir()
        evil.write_text(f"open({str(canary)!r}, 'w').close()\n", encoding="utf-8")
        exe = pathlib.Path(sys.executable).name
        for where in ((".claude", "settings.json"), (".codex", "hooks.json")):
            path = pathlib.Path(td).joinpath(*where)
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command":
                    f'{exe} "{evil.as_posix()}" hook pre-tool'}]}]}}),
                encoding="utf-8")
        (pathlib.Path(td) / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "pythia": {"command": exe, "args": [evil.as_posix(), "mcp"]}}}),
            encoding="utf-8")
        rows = pythia.harness_checks(td, home)
        assert not canary.exists(), rows
        named = [m for s, a, m in rows if s == "warn" and "shares" in m]
        assert len(named) == 3, rows


# Field test of 0.15.2: right after an install from a venv, doctor reported
# `fail` for the team's shared hooks ("the guard is off") although
# settings.local.json served every one of them, and `fail` for Codex's session
# hook, which carries no guard at all — and advised a re-run of install that
# cannot change either, since install leaves shared files alone.

GONE = "python3.99 -m pythia"           # a bare interpreter on no PATH


def _launch_rows(rows, prefix):
    return [(s, m) for s, a, m in rows if a == "hook launch" and m.startswith(prefix)]


def test_doctor_a_shared_hook_this_machine_already_covers_is_a_warning():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_hooks(td, launcher=GONE)                  # the team's
        pythia.install_claude_hooks(td, launcher=_good_launcher(), local=True)
        rows = pythia.harness_checks(td, home)
        shared = _launch_rows(rows, ".claude/settings.json")
        assert len(shared) == 1 and shared[0][0] == "warn", rows
        msg = shared[0][1]
        assert ".claude/settings.local.json" in msg      # what serves them here
        assert "keep" in msg and "remove" in msg          # the developer's call
        assert "error" in msg                             # what they will see
        assert not [r for r in rows if r[0] == "fail"], rows


def test_doctor_a_shared_hook_nothing_here_covers_stays_a_fail_with_a_real_fix():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_hooks(td, launcher=GONE)
        local = pathlib.Path(td) / ".claude" / "settings.local.json"
        local.write_text(json.dumps({"hooks": {"PreToolUse": [         # the guard only
            {"matcher": "Bash", "hooks": [{"type": "command",
             "command": f"{_good_launcher()} hook pre-tool"}]}]}}), encoding="utf-8")
        rows = pythia.harness_checks(td, home)
        shared = _launch_rows(rows, ".claude/settings.json")
        assert len(shared) == 1 and shared[0][0] == "fail", rows
        msg = shared[0][1]
        assert "not on PATH" in msg and "install" in msg
        assert ".claude/settings.local.json" in msg       # where the fix lands
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_hooks(td, launcher=GONE)    # no local file at all
        assert _launch_rows(pythia.harness_checks(td, home),
                            ".claude/settings.json")[0][0] == "fail"


def test_doctor_names_exactly_what_a_broken_hook_leaves_off():
    """~/.claude (install -g) serves the guard and the approval hook but no
    SessionStart: the team's broken copy then leaves off only the session
    guide — a warning that says so, not "the guard is off"."""
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_hooks(td, launcher=GONE)
        pythia.install_claude_hooks(home, events=("PreToolUse", "PostToolUse"),
                                    launcher=_good_launcher())
        rows = pythia.harness_checks(td, home)
        shared = _launch_rows(rows, ".claude/settings.json")
        assert len(shared) == 1 and shared[0][0] == "warn", rows
        assert "session guide" in shared[0][1] and "guard" not in shared[0][1]
        assert not [r for r in rows if r[0] == "fail"], rows
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_hooks(td, launcher=GONE)
        local = pathlib.Path(td) / ".claude" / "settings.local.json"
        local.write_text(json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command",
             "command": f"{_good_launcher()} hook pre-tool"}]}]}}), encoding="utf-8")
        msg = _launch_rows(pythia.harness_checks(td, home), ".claude/settings.json")[0][1]
        assert "AskUserQuestion approval" in msg and "sqlplus guard" not in msg


def test_doctor_codex_session_hook_names_its_real_scope():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        p = pathlib.Path(td) / ".codex" / "hooks.json"
        p.parent.mkdir()
        p.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": f"{GONE} hook session-start"}]}]}}),
            encoding="utf-8")
        rows = pythia.harness_checks(td, home)
        codex = _launch_rows(rows, ".codex/hooks.json")
        assert len(codex) == 1 and codex[0][0] == "warn", rows
        assert "AGENTS.md" in codex[0][1] and "session" in codex[0][1]
        assert "guard" not in codex[0][1]                 # it carries none
        assert not [r for r in rows if r[0] == "fail"], rows


def test_doctor_names_what_a_broken_mcp_launcher_closes():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        (pathlib.Path(td) / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "pythia": {"command": "python3.99", "args": ["-m", "pythia", "mcp"]}}}),
            encoding="utf-8")
        door = [(s, m) for s, a, m in pythia.harness_checks(td, home)
                if a == "approval door"]
        assert len(door) == 1 and door[0][0] == "fail", door
        assert "pythia_approve" in door[0][1] and "local scope" in door[0][1]
        assert "guard" not in door[0][1]                  # a door, not the guard


def test_doctor_sends_a_broken_global_hook_to_install_dash_g():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as home:
        pythia.install_claude_hooks(home, events=("PreToolUse", "PostToolUse"),
                                    launcher='"/nonexistent/bin/python" -m pythia')
        user = _launch_rows(pythia.harness_checks(td, home), "~/.claude/settings.json")
        assert len(user) == 1 and user[0][0] == "fail", user
        assert "install -g" in user[0][1]


def test_doctor_warns_a_bare_launcher_that_lives_in_the_active_virtualenv():
    ok = lambda l, cwd=None: (True, "")                       # noqa: E731
    with tempfile.TemporaryDirectory() as venv:
        st, msg = pythia.launcher_health(
            "python -m pythia", cwd=venv, which=lambda n: f"{venv}/bin/{n}",
            env={"VIRTUAL_ENV": venv}, starts=ok)
        assert st == "warn" and "virtual environment" in msg
        st, _ = pythia.launcher_health(
            "python -m pythia", cwd=venv, which=lambda n: "/usr/bin/python",
            env={"VIRTUAL_ENV": venv}, starts=ok)
        assert st == "ok"


def test_pre_tool_denies_approve_hook_in_any_launcher_spelling():
    with tempfile.TemporaryDirectory() as td:
        for cmd in ('"/v/bin/python" -m pythia approve --hook',
                    "/v/bin/python /s/pythia.py approve --hook",
                    "python3 -m pythia approve --hook"):
            assert decision(pre(td, cmd)) == "deny", cmd


def test_claude_md_says_what_a_session_without_pythia_hooks_means():
    """If the hooks cannot start, only CLAUDE.md is left to say so."""
    block = pythia.claude_md_block()
    assert "STATE" in block and "pythia doctor" in block
    assert "guard" in block and "pythia_approve" in block


def test_cmd_install_puts_a_machine_specific_launcher_in_local_files_only():
    import argparse
    launcher = '"/v/bin/python" -m pythia'
    old = (pythia.choose_launcher, pythia.register_local_mcp,
           os.environ.get("PATH"))
    reg = []
    pythia.choose_launcher = lambda **kw: (
        launcher, False, "pythia runs from the virtual environment /v")
    pythia.register_local_mcp = lambda root, l, **kw: (
        reg.append(l) or ("added", "claude mcp add --scope local pythia -- x"))
    os.environ["PATH"] = ""                  # no npx: bundled skills, offline
    try:
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            (base / ".git" / "info").mkdir(parents=True)
            (base / ".claude").mkdir()
            (base / ".claude" / "settings.json").write_text(json.dumps(
                {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                    {"type": "command",
                     "command": "python -m pythia hook pre-tool"}]}]}}),
                encoding="utf-8")
            shared_before = (base / ".claude" / "settings.json").read_text(
                encoding="utf-8")
            ns = argparse.Namespace(project_root=td, glob=False, source=None,
                                    color=False, json=False, no_hooks=False)
            _, out = quiet(pythia.cmd_install, None, None, ns)
            assert (base / ".claude" / "settings.local.json").is_file()
            assert (base / ".claude" / "settings.json").read_text(
                encoding="utf-8") == shared_before          # shared: untouched
            mcp = base / ".mcp.json"
            assert not mcp.exists() or "/v/bin" not in mcp.read_text(encoding="utf-8")
            assert reg == [launcher]
            assert "settings.local.json" in out and "virtual environment" in out
            assert "also" in out and "settings.json" in out  # the shared copy, named
            assert "~/.codex/config.toml" in out              # Codex, not silent
            exclude = base / ".git" / "info" / "exclude"
            assert ".claude/settings.local.json" in exclude.read_text(encoding="utf-8")
            quiet(pythia.cmd_install, None, None, ns)          # re-run
            assert exclude.read_text(encoding="utf-8").count(
                ".claude/settings.local.json") == 1
    finally:
        pythia.choose_launcher, pythia.register_local_mcp = old[0], old[1]
        if old[2] is not None:
            os.environ["PATH"] = old[2]


def test_lint_counts_comments_from_every_file_in_the_set():
    """A release keeps its COMMENT ON statements in their own file — the
    order deploy runs them in. Linting the set must not call them missing."""
    import argparse
    with tempfile.TemporaryDirectory() as td:
        a = pathlib.Path(td) / "01_t_order.sql"
        b = pathlib.Path(td) / "02_comments.sql"
        a.write_text("create table t_order (id number(12) not null, "
                     "note varchar2(10 char));\n", encoding="utf-8")
        b.write_text("comment on table t_order is 'Orders';\n"
                     "comment on column t_order.id is 'Key';\n"
                     "comment on column t_order.note is 'Free text';\n",
                     encoding="utf-8")

        def lint(*paths):
            ns = argparse.Namespace(paths=[str(p) for p in paths], json=True,
                                    project_root=td, color=False)
            _, out = quiet(pythia.cmd_lint, None, None, ns)
            return json.loads(out)["files"]

        assert not [f for f in lint(a, b)[str(a)] if f["rule"] == "comment-missing"]
        alone = [f for f in lint(a)[str(a)] if f["rule"] == "comment-missing"]
        assert alone and "in this script" in alone[0]["message"]


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
