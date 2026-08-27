-- bench fixture: the same job written the way patterns.md teaches.
-- bench.py --selftest fails if the linter flags anything here.
CREATE OR REPLACE PROCEDURE bench_good(p_out IN OUT NOCOPY CLOB) AS
  v_name t_order.name%TYPE;
  CURSOR c_new IS SELECT id FROM t_order WHERE status = 'NEW';
  TYPE t_ids IS TABLE OF t_order.id%TYPE;
  v_ids t_ids;
BEGIN
  OPEN c_new;
  FETCH c_new BULK COLLECT INTO v_ids LIMIT 500;
  CLOSE c_new;
  FORALL i IN 1 .. v_ids.COUNT
    UPDATE t_order SET status = 'DONE' WHERE id = v_ids(i);
  EXECUTE IMMEDIATE 'DELETE FROM t_log WHERE id = :1' USING v_name;
  COMMIT;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RAISE;
END bench_good;
/
