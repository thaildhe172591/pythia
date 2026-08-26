# PL/SQL patterns worth copying

Generic patterns for when the codebase gives no model to imitate. When the
codebase disagrees with this file, **the codebase wins** — consistency beats
preference.

## Anchor declarations to the dictionary

```sql
-- fragile: copies today's type by hand
v_customer_name VARCHAR2(100);

-- survives column changes
v_customer_name customers.name%TYPE;
r_order         orders%ROWTYPE;
```

## Bulk over row-by-row

```sql
-- slow at scale: one context switch per row
FOR r IN (SELECT id FROM t_order WHERE status = 'NEW') LOOP
  process_order(r.id);
END LOOP;

-- bulk: fetch in batches, write with FORALL
DECLARE
  TYPE t_ids IS TABLE OF t_order.id%TYPE;
  v_ids t_ids;
  CURSOR c IS SELECT id FROM t_order WHERE status = 'NEW';
BEGIN
  OPEN c;
  LOOP
    FETCH c BULK COLLECT INTO v_ids LIMIT 500;
    EXIT WHEN v_ids.COUNT = 0;
    FORALL i IN 1 .. v_ids.COUNT
      UPDATE t_order SET status = 'DONE' WHERE id = v_ids(i);
  END LOOP;
  CLOSE c;
END;
```

The `LIMIT` matters: unbounded `BULK COLLECT` trades the row-switch problem
for a memory problem.

## Exceptions: handle what you can name, re-raise the rest

```sql
BEGIN
  ...
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RETURN NULL;                       -- a decision, made on purpose
  WHEN OTHERS THEN
    log_error(sqlcode, sqlerrm, 'PKG_ORDER.calc_total');
    RAISE;                             -- never swallow; the caller must know
END;
```

`WHEN OTHERS` without `RAISE` (or `RAISE_APPLICATION_ERROR`) converts every
future bug into silent wrong data.

## Dynamic SQL: binds, never concatenation

```sql
-- injection + hard parse per call
EXECUTE IMMEDIATE 'SELECT total FROM t_order WHERE id = ' || p_id INTO v_t;

-- shared cursor, safe
EXECUTE IMMEDIATE 'SELECT total FROM t_order WHERE id = :1'
  INTO v_t USING p_id;
```

Identifiers (table names) cannot be bound — validate them against
`ALL_TABLES`/`ALL_OBJECTS` before splicing, and say so in a comment.

## Commit ownership

Utility and business procedures do not `COMMIT`; the outermost caller — the
one that knows the transaction's boundaries — does. A procedure that commits
mid-loop turns a failed run into a half-applied state nobody can reason
about, and breaks the caller's ability to roll back.

## Large OUT parameters

```sql
PROCEDURE render_report(p_clob IN OUT NOCOPY CLOB);
```

Without `NOCOPY`, IN OUT copies the value both ways; for big CLOBs and
collections that is real memory and time. (It is a hint, not a guarantee —
still worth writing.)

## Naming: mine, don't invent

Before naming anything: `pythia similar <intended_name>` and copy the
dominant token order, prefixes and casing of the top hits. Parameter
prefixes, cursor names, and constant style come from `pythia src` of a
neighboring program — not from this file.
