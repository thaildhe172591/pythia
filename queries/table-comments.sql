-- Purpose: the comment on one table and on each of its columns, with the
--          column's type — the data dictionary as documentation, and which
--          parts of it are still blank. The table's own row comes first, with
--          a null COLUMN_NAME.
-- Binds:   :s  schema (table owner)
--          :n  table or view name
-- Returns: TABLE_NAME, COLUMN_NAME, DATA_TYPE, COMMENTS, COLUMN_ID
select t.table_name, null column_name, t.table_type data_type, t.comments,
       0 column_id
  from all_tab_comments t
 where t.owner = :s
   and t.table_name = upper(:n)
union all
select c.table_name, c.column_name,
       c.data_type || case
         when c.data_type in ('VARCHAR2', 'NVARCHAR2', 'CHAR', 'NCHAR', 'RAW')
           then '(' || c.char_length
                || case c.char_used when 'C' then ' CHAR' else '' end || ')'
         when c.data_type = 'NUMBER' and c.data_precision is not null
           then '(' || c.data_precision || ',' || c.data_scale || ')'
       end data_type,
       m.comments, c.column_id
  from all_tab_columns c
  left join all_col_comments m
    on m.owner = c.owner
   and m.table_name = c.table_name
   and m.column_name = c.column_name
 where c.owner = :s
   and c.table_name = upper(:n)
 order by 5
