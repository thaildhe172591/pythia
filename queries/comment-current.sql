-- Purpose: the comment a COMMENT ON statement is about to replace — the
--          snapshot for the comments group. One dictionary read is the whole
--          undo: restore writes this text back. A column comment when :c is
--          given, the table comment when :c is null.
-- Binds:   :s  schema (table owner)
--          :t  table name, exactly as stored
--          :c  column name, exactly as stored, or null for the table comment
-- Returns: COMMENTS
select comments
  from all_col_comments
 where owner = :s
   and table_name = :t
   and column_name = :c
union all
select comments
  from all_tab_comments
 where owner = :s
   and table_name = :t
   and :c is null
