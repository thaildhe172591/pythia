# PL/SQL antipatterns

Seven findings that recur in real codebases. Each: how it looks, what to
write instead, and why it bites. Anchor findings to line numbers from
`pythia src`.

## 1. Row-by-row cursor loop doing DML

**Wrong**
```sql
FOR r IN (SELECT id FROM t_order WHERE status = 'NEW') LOOP
  UPDATE t_order SET status = 'DONE' WHERE id = r.id;
END LOOP;
```
**Right** — `BULK COLLECT ... LIMIT` + `FORALL` (see patterns.md for the
full template), or better: one set-based `UPDATE` when no per-row logic
exists.
**Why** — a context switch per row; thousands of rows means thousands of
switches. The set-based form is also atomic.

## 2. Dynamic SQL built by concatenation

**Wrong**
```sql
EXECUTE IMMEDIATE 'DELETE FROM t_log WHERE id = ' || p_id;
```
**Right**
```sql
EXECUTE IMMEDIATE 'DELETE FROM t_log WHERE id = :1' USING p_id;
```
**Why** — SQL injection when the value is user-reachable, and a hard parse
per distinct value even when it is not: shared-pool churn that hits the
whole instance, not just this session.

## 3. `WHEN OTHERS THEN NULL`

**Wrong**
```sql
EXCEPTION WHEN OTHERS THEN NULL;
```
**Right** — handle the exceptions you can name; for the rest, log and
`RAISE`.
**Why** — every future bug in the block becomes silent wrong data. The
worst version is around a `SELECT INTO`, where it also hides
`TOO_MANY_ROWS` — a data-quality alarm.

## 4. `COMMIT` inside a loop

**Wrong**
```sql
FOR r IN c LOOP
  process(r);
  COMMIT;                -- "to be safe"
END LOOP;
```
**Right** — commit once, at the transaction's owner (usually the outermost
caller). For huge volumes, batch commits at a documented interval are a
deliberate, named decision — not a reflex.
**Why** — a failure mid-loop leaves a half-applied state that can be
neither completed nor rolled back; it also breaks any caller that thought
it owned the transaction, and `ORA-01555` risk goes up, not down.

## 5. Hand-copied types instead of `%TYPE` / `%ROWTYPE`

**Wrong**
```sql
v_name VARCHAR2(50);     -- the column is VARCHAR2(100) since 2024
```
**Right**
```sql
v_name customers.name%TYPE;
```
**Why** — the declaration is a snapshot that silently drifts from the
schema; the failure arrives later as `VALUE_ERROR` on production data.

## 6. Large OUT parameters without `NOCOPY`

**Wrong**
```sql
PROCEDURE render(p_out IN OUT CLOB);
```
**Right**
```sql
PROCEDURE render(p_out IN OUT NOCOPY CLOB);
```
**Why** — IN OUT copies on entry and exit; for CLOBs and big collections
that is real memory and time per call.

## 7. Style drift from the codebase

**Wrong** — new naming scheme, new cursor style, new comment format,
introduced silently in one procedure.
**Right** — `pythia similar` + `pythia src` on the top hits; match them.
Propose style improvements to the developer as a separate conversation.
**Why** — a mixed-style codebase costs every future reader; consistency is
a feature maintainers can feel.

## Reporting format

```
line 47: WHEN OTHERS THEN NULL — swallows every error including
         TOO_MANY_ROWS — handle NO_DATA_FOUND explicitly, log and RAISE the rest
```

Severity order: correctness → silent data/error loss → performance at
scale → style.
