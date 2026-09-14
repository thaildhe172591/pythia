#!/usr/bin/env python3
"""MCP approver self-checks — no database, no network, no live Codex.

Run: python tests/test_mcp.py
"""
import io
import json
import os
import pathlib
import sys
import tempfile

os.environ.setdefault("PYTHIA_CI", "1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import pythia  # noqa: E402


# --- Task 1: transport handshake + tools/list -------------------------------

def test_initialize_echoes_protocol_and_declares_tools():
    out, done = pythia.mcp_handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "codex", "version": "0.120.0"}}},
        {})
    assert done is False
    assert len(out) == 1
    r = out[0]
    assert r["id"] == 1 and r["jsonrpc"] == "2.0"
    assert r["result"]["protocolVersion"] == "2025-06-18"     # echo client's
    assert "tools" in r["result"]["capabilities"]
    assert r["result"]["serverInfo"]["name"] == "pythia"


def test_initialized_notification_is_silent():
    out, done = pythia.mcp_handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, {})
    assert out == [] and done is False


def test_tools_list_returns_only_pythia_approve():
    out, _ = pythia.mcp_handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {})
    tools = out[0]["result"]["tools"]
    assert [t["name"] for t in tools] == ["pythia_approve"]
    schema = tools[0]["inputSchema"]
    assert schema["properties"]["token"]["type"] == "string"
    assert schema["required"] == ["token"]


def test_unknown_method_with_id_is_an_error_not_a_crash():
    out, _ = pythia.mcp_handle(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, {})
    assert out[0]["error"]["code"] == -32601        # method not found
    assert out[0]["id"] == 3


# --- Task 2: the elicitation round-trip and the mint (fail-closed) ----------

def _preview(root, token="a1b2c3"):
    """A pending plsql_source preview in the journal, the way apply writes one."""
    pythia.write_journal_entry(
        root, "PACKAGE BODY", "PKG_ORDER", "old\n",
        "CREATE OR REPLACE PACKAGE BODY pkg_order AS\n new;\nEND;\n",
        {"token": token, "connection": "DEV", "schema": "APP",
         "group": "plsql_source", "applied": False})
    return token


def test_mint_only_on_accept_with_approve():
    with tempfile.TemporaryDirectory() as td:
        token = _preview(td)
        seen = {}

        def elicit(message, schema):
            seen["message"] = message
            seen["schema"] = schema
            return "accept", {"decision": "Approve"}

        res = pythia.mcp_approve_decision(td, token, elicit)
        assert res["isError"] is False
        assert token in res["content"][0]["text"]
        assert "PKG_ORDER" in seen["message"]                 # the card was shown
        assert seen["schema"]["properties"]["decision"]["enum"] == ["Approve", "Reject"]
        g = pythia.read_grant(td, token)
        assert g and g["approver"] == "mcp" and g["used_at"] is None


def test_no_mint_on_reject_decline_or_cancel():
    for action, content in (("accept", {"decision": "Reject"}),
                            ("decline", {}), ("cancel", {})):
        with tempfile.TemporaryDirectory() as td:
            token = _preview(td)
            res = pythia.mcp_approve_decision(td, token, lambda m, s: (action, content))
            assert res["isError"] is True
            assert pythia.read_grant(td, token) is None       # nothing minted


def test_no_mint_for_unknown_token_and_elicit_is_not_even_called():
    with tempfile.TemporaryDirectory() as td:
        called = []
        res = pythia.mcp_approve_decision(
            td, "nope99",
            lambda m, s: called.append(1) or ("accept", {"decision": "Approve"}))
        assert res["isError"] is True
        assert called == []                                   # refused before asking
        assert pythia.read_grant(td, "nope99") is None


def test_elicit_error_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        token = _preview(td)

        def boom(message, schema):
            raise RuntimeError("transport died")

        res = pythia.mcp_approve_decision(td, token, boom)
        assert res["isError"] is True
        assert pythia.read_grant(td, token) is None


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
