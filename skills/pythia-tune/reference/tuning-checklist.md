# Tuning checklist

Work top to bottom: each step is cheaper and more common than the next.
Record what you looked at — the before/after comparison needs it.

## 0. Capture

- [ ] The exact statement, with binds as `:name` (not literals).
- [ ] `pythia explain "<select>"` saved (plan hash, cost, rows per line).
- [ ] `pythia stats <TABLE>` for every table in the plan.
- [ ] How often it runs: once per report, or once per row of something
      else? (`pythia grep` the caller.) A 5 ms query called 100,000 times
      is the real problem.

## 1. Statistics

- [ ] `LAST_ANALYZED` present and recent; `STALE_STATS` not `YES`.
- [ ] Plan rows (cardinality) roughly match reality — check with
      `pythia sql "select count(*) from t where <predicate>"`.
      Off by 10× or more: the optimizer is working from bad numbers, and
      every other fix is built on sand.
- [ ] Fix belongs to the owner/DBA: `DBMS_STATS.GATHER_TABLE_STATS`
      (with `cascade => TRUE` for the indexes). Skewed columns may need a
      histogram — the default `METHOD_OPT` usually decides that itself.

## 2. Sargable predicates — can an index be used at all?

| Not sargable | Sargable |
|---|---|
| `WHERE TRUNC(created_at) = :d` | `WHERE created_at >= :d AND created_at < :d + 1` |
| `WHERE UPPER(code) = :c` | function-based index on `UPPER(code)`, or store it normalised |
| `WHERE TO_CHAR(id) = :s` / `id = '123'` on a VARCHAR2 key compared to a number | compare like types; convert the bind, never the column |
| `WHERE name LIKE '%abc'` | Oracle Text, or a different access path |
| `WHERE NVL(status, 'N') = 'N'` | `WHERE (status = 'N' OR status IS NULL)`, or `NOT NULL DEFAULT 'N'` on the column |
| `WHERE a = :x OR b = :y` (different columns) | `UNION ALL` of two indexed branches (check duplicates) |

`INTERNAL_FUNCTION(...)` in the plan's predicate section is the optimizer
telling you it converted a column — the index on it is out.

## 3. Indexes

- [ ] The predicate columns have an index whose **leading** columns match.
- [ ] Equality columns first, range column last.
- [ ] Selectivity: an index helps when it returns a small share of the
      table (rule of thumb: a few percent); above that a full scan is often
      right, and the optimizer knows it.
- [ ] Clustering factor (`pythia stats`) close to the table's blocks = good;
      close to its rows = scattered, range scans read many blocks.
- [ ] Covering: adding the selected columns to the index can avoid the
      table access entirely — at a write cost.
- [ ] Unindexed foreign keys (listed by `pythia stats`) — locking, not just
      speed.
- [ ] Invisible index (`VISIBILITY = INVISIBLE`) to test without changing
      other plans — it is still maintained on every write.

## 4. Join shape

- [ ] Every table joined by a predicate (no `CARTESIAN` unless intended).
- [ ] Nested loops for few driving rows with an indexed inner side; hash
      join for large sets. Wrong cardinality (step 1) picks the wrong one.
- [ ] `NOT IN (subquery)` over a nullable column → `NOT EXISTS`.
- [ ] `IN` lists of thousands of literals → a join to a GTT or a collection.
- [ ] `SELECT DISTINCT` hiding a missing join predicate.
- [ ] Pagination: `FETCH FIRST n ROWS ONLY` / `ROWNUM` on an ordered inline
      view — never `ROWNUM <= n ... ORDER BY` at one level.

## 5. PL/SQL around the SQL

- [ ] SQL inside a loop over another query's rows → one set-based
      statement, or `BULK COLLECT ... LIMIT 500` + `FORALL`.
- [ ] `FORALL ... SAVE EXCEPTIONS` when partial success is the spec.
- [ ] A PL/SQL function called per row from SQL: scalar subquery caching
      (`(SELECT f(x) FROM dual)`), `DETERMINISTIC`, `PRAGMA UDF` (12c),
      `RESULT_CACHE` for stable lookup data — or inline the logic in SQL.
- [ ] `SELECT COUNT(*) INTO` just to test existence → `EXISTS` / fetch
      first row.
- [ ] Commits in loops (also a correctness issue — see pythia-review).
- [ ] Dynamic SQL with concatenated literals → binds (parse cost, shared
      pool, and injection).
- [ ] Large `IN OUT` collections/LOBs → `NOCOPY`.

## 6. Last resorts — each needs the developer's explicit yes

- Hints (`/*+ INDEX(...) */`, `LEADING`, `USE_HASH`) — freeze a plan
  against future data. Prefer fixing statistics or SQL.
- SQL Plan Baselines / SQL Profiles — DBA territory; profiles need the
  Tuning Pack.
- Materialized views, partitioning, compression — design changes, some
  licensed separately (Partitioning, Advanced Compression).
- Parallel query — consumes the whole machine; rarely right for OLTP.

## Report format

```
Before: plan hash 1234, cost 18,400 — TABLE ACCESS FULL T_ORDER (2.1M rows)
Cause:  TRUNC(created_at) = :d disables IX_T_ORDER_CREATED_AT (plan line 3)
Change: predicate rewritten to a range in PKG_REPORT.daily_orders (journal <entry>)
After:  plan hash 5678, cost 42 — INDEX RANGE SCAN IX_T_ORDER_CREATED_AT
Not measured: runtime under production load (dev schema has 2% of the rows)
```
