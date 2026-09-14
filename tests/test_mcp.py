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
