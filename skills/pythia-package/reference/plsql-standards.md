# PL/SQL standards — the defaults beneath the house rules

Distilled from Oracle's PL/SQL Language Reference, the Trivadis PL/SQL &
SQL Coding Guidelines, and long-standing community practice. **Precedence:
`.pythia/conventions.md` → the codebase (`pythia similar`) → this file.**
Never introduce a style here that the codebase does not use; propose it.

## Naming (only when the house has no rule)

| Item | Prefix | Example |
|---|---|---|
| Local variable | `l_` | `l_total` |
| Global (package) variable | `g_` | `g_initialized` |
| Constant | `co_` | `co_max_lines` |
| IN / OUT / IN OUT parameter | `in_` / `out_` / `io_` (or the house's `p_`) | `in_order_id` |
| Cursor | `c_` | `c_open_orders` |
| Record | `r_` | `r_order` |
| Collection / array | `t_` | `t_ids` |
| Exception | `e_` | `e_order_not_found` |
| Type | `_type` suffix | `t_id_list_type` |

`conventions.json` `"lint": {"param_prefix": "^P_"}` makes the parameter
rule machine-checked at every lint and apply preview.

## Declarations

- Anchor to the dictionary: `l_name t_order.note%TYPE`, `r_order t_order%ROWTYPE`.
- `PLS_INTEGER` / `SIMPLE_INTEGER` for counters and loop indexes, not `NUMBER`.
- Constants for every magic number and literal code (`co_status_new CONSTANT
  t_order.status%TYPE := 'N';`).
- `SUBTYPE` for repeated domain types.

## Control flow

- One way in, one way out of a function: a single final `RETURN`, or clear
  guard clauses at the top.
- `CASE` over long `IF ... ELSIF` chains; `EXIT WHEN` over `IF ... EXIT`.
- No `GOTO`. No unbounded `LOOP` without an `EXIT` you can point to.

## SQL inside PL/SQL

- Static SQL binds automatically — prefer it. `SELECT ... INTO` for exactly
  one row, and handle `NO_DATA_FOUND` / `TOO_MANY_ROWS` deliberately.
- Many rows: cursor `FOR` loop only to *read*; to write, `BULK COLLECT ...
  LIMIT 500` and `FORALL` (`SAVE EXCEPTIONS` when partial success is the spec).
- `RETURNING ... INTO` instead of a second `SELECT` after DML.
- Existence: `SELECT 1 ... FETCH FIRST 1 ROW ONLY` / `EXISTS`, never
  `COUNT(*)`.
- Dynamic SQL only when static cannot work; binds via `USING`; identifiers
  through `DBMS_ASSERT.ENQUOTE_NAME` / `SQL_OBJECT_NAME`.

## Exceptions

```sql
e_order_not_found EXCEPTION;
PRAGMA EXCEPTION_INIT(e_order_not_found, -20101);    -- spec: callers can catch it

...
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RAISE_APPLICATION_ERROR(-20101, 'order ' || in_order_id || ' not found');
  WHEN OTHERS THEN
    logger_pkg.log_error(co_unit, SQLCODE, SQLERRM,
                         DBMS_UTILITY.FORMAT_ERROR_BACKTRACE);
    RAISE;                                           -- never swallow
END;
```

- Handle what you can name; log and re-raise the rest.
- `RAISE_APPLICATION_ERROR(code, msg, TRUE)` keeps the original error stack.
- One `-20xxx` range per package, documented in the spec.
- Log at the boundary (the public entry point), not in every layer.

## Transactions

- The outermost caller commits or rolls back. Units do not `COMMIT` unless
  their contract says so.
- `SAVEPOINT` + `ROLLBACK TO` for a step that may fail without undoing the
  caller's work.
- `PRAGMA AUTONOMOUS_TRANSACTION` for logging/audit writes only — it must
  commit or roll back itself before returning.
- DDL through `EXECUTE IMMEDIATE` commits the caller's transaction: avoid it
  in transactional code.

## Functions and SQL

- Functions called from SQL: no DML, no package-state changes, no OUT
  parameters. Mark `DETERMINISTIC` only when true; `PRAGMA UDF` (12c) for
  functions designed for SQL; `RESULT_CACHE` for stable lookups.
- Calling a function per row from a big query is a context switch per row —
  prefer SQL expressions or scalar subquery caching.

## Triggers

- Thin: derive a column, stamp audit columns, enforce what a constraint
  cannot. No business process, no `COMMIT`, no remote calls.
- Row-level `WHEN (...)` clause to fire only when needed.
- Several timing points on one table → **one compound trigger**; it is also
  the fix for ORA-04091 (mutating table): collect in the row section, act in
  `AFTER STATEMENT`.
- Order between triggers with `FOLLOWS` / `PRECEDES`, never by hoping.

```sql
CREATE OR REPLACE TRIGGER trg_t_order_audit
  BEFORE INSERT OR UPDATE ON t_order
  FOR EACH ROW
BEGIN
  IF INSERTING THEN
    :NEW.created_by := SYS_CONTEXT('USERENV', 'SESSION_USER');
    :NEW.created_at := SYSTIMESTAMP;
  ELSE
    :NEW.updated_by := SYS_CONTEXT('USERENV', 'SESSION_USER');
    :NEW.updated_at := SYSTIMESTAMP;
  END IF;
END trg_t_order_audit;
/
```

## Security

- `AUTHID DEFINER` (the default) unless the unit must run with the caller's
  rights; `AUTHID CURRENT_USER` changes name resolution — test it.
- `ACCESSIBLE BY (pkg_x)` (12c+) for helpers no one else should call.
- Never build SQL from user input by concatenation; never store passwords.
- Grants go to roles, not users; `EXECUTE` on the API package, not `SELECT`
  on the tables behind it.

## Performance defaults

- `NOCOPY` on large `IN OUT` collections and LOBs.
- Bulk over row-by-row (see `pythia-tune/reference/tuning-checklist.md`).
- Package state (globals) is per session and survives calls — initialise it
  deliberately, and expect ORA-04068 when the body is recompiled under a
  live session.

## Templates

```sql
CREATE OR REPLACE PACKAGE pkg_order AS
  -- errors: -20101 order not found, -20102 order closed
  e_order_not_found EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_order_not_found, -20101);

  FUNCTION total(in_order_id IN t_order.id%TYPE) RETURN t_order.amount%TYPE;
  PROCEDURE close_order(in_order_id IN t_order.id%TYPE);   -- does not commit
END pkg_order;
/
```

```sql
CREATE OR REPLACE PACKAGE BODY pkg_order AS
  co_unit CONSTANT VARCHAR2(30) := 'PKG_ORDER';

  FUNCTION total(in_order_id IN t_order.id%TYPE) RETURN t_order.amount%TYPE IS
    l_total t_order.amount%TYPE;
  BEGIN
    SELECT SUM(qty * price) INTO l_total
      FROM t_order_line
     WHERE order_id = in_order_id;
    RETURN NVL(l_total, 0);
  END total;

  PROCEDURE close_order(in_order_id IN t_order.id%TYPE) IS
  BEGIN
    UPDATE t_order SET status = 'C' WHERE id = in_order_id;
    IF SQL%ROWCOUNT = 0 THEN
      RAISE_APPLICATION_ERROR(-20101, 'order ' || in_order_id || ' not found');
    END IF;
  END close_order;
END pkg_order;
/
```

## Unit tests (utPLSQL v3)

```sql
CREATE OR REPLACE PACKAGE ut_pkg_order AS
  --%suite(pkg_order)
  --%test(total of an order without lines is 0)
  PROCEDURE total_without_lines;
  --%test(closing an unknown order raises -20101)
  --%throws(-20101)
  PROCEDURE close_unknown;
END ut_pkg_order;
/
```

Tests are packages, so they land through `pythia apply` like any code.
Running them (`ut.run`) executes code — that is the developer's or CI's
step; pythia refuses anonymous blocks by design. `pythia doctor --db` says
whether utPLSQL is installed.
