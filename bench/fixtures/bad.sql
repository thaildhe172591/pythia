-- bench fixture: every mechanically detectable antipattern, once.
-- bench.py --selftest fails if the linter misses any of these.
CREATE OR REPLACE PROCEDURE bench_bad(p_out IN OUT CLOB) AS
  v_name VARCHAR2(50);
  v_amt  NUMBER(10,2);
BEGIN
  FOR r IN (SELECT id FROM t_order WHERE status = 'NEW') LOOP
    UPDATE t_order SET status = 'DONE' WHERE id = r.id;
    COMMIT;
  END LOOP;
  EXECUTE IMMEDIATE 'DELETE FROM t_log WHERE id = ' || v_name;
EXCEPTION
  WHEN OTHERS THEN NULL;
END bench_bad;
/
