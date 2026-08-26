#!/usr/bin/env python3
"""Phase 2 self-checks for pythia.py — pure logic only, no database needed.

Run: python tests/test_phase2.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import pythia  # noqa: E402


def test_query_binds_ignores_comments_and_literals():
    sql = """-- Purpose: demo, mentions :notabind in a comment
             select to_char(d, 'hh24:mi:ss') txt
               from t where owner = :s and name = upper(:n)"""
    assert pythia.query_binds(sql) == {"s", "n"}


def test_query_bind_contract_matches_files():
    on_disk = {p.name for p in pythia.QUERY_DIR.glob("*.sql")}
    assert on_disk == set(pythia.QUERY_BINDS), (
        f"queries/ and QUERY_BINDS drifted: "
        f"only on disk {on_disk - set(pythia.QUERY_BINDS)}, "
        f"only declared {set(pythia.QUERY_BINDS) - on_disk}")
    for name, declared in pythia.QUERY_BINDS.items():
        actual = pythia.query_binds(pythia.load_query(name))
        assert actual == declared, f"{name}: file uses {actual}, code declares {declared}"


def test_every_query_has_a_header_comment():
    for name in pythia.QUERY_BINDS:
        text = pythia.load_query(name)
        for field in ("-- Purpose:", "-- Binds:", "-- Returns:"):
            assert field in text, f"{name} is missing a '{field}' header line"


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001 — report every failure, keep going
                failed += 1
                print(f"FAIL {name}: {e}")
    if failed:
        sys.exit(f"{failed} test(s) failed")
    print("OK")


if __name__ == "__main__":
    main()
