-- Purpose: probe whether this session can read DBA_DEPENDENCIES. When it
--          cannot (ORA-00942), ALL_DEPENDENCIES lists only dependents the
--          session has access to, so impact says other schemas may hide
--          some. One row at most: the answer is "readable", not a count.
-- Binds:   (none)
-- Returns: N (1 when any dependency row exists)
select count(*) n
  from dba_dependencies
 where rownum = 1
