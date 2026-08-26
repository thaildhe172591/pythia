-- Purpose: the pool of programs a new one could be modelled on. Ranking runs
--          in Python (rank_similar) where it is unit-testable; splitting names
--          in SQL would need a recursive CTE and make this file unreviewable.
-- Binds:   :s  schema (object owner)
-- Returns: OBJECT_NAME, OBJECT_TYPE, STATUS, LAST_DDL
select object_name,
       object_type,
       status,
       to_char(last_ddl_time, 'yyyy-mm-dd') last_ddl
  from all_objects
 where owner = :s
   and object_type in ('PROCEDURE', 'FUNCTION', 'PACKAGE')
 order by object_name
