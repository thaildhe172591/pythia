-- Purpose: every index on one table with its columns in order, uniqueness,
--          status and the statistics that decide whether the optimizer will
--          use it (distinct keys, clustering factor).
-- Binds:   :s  schema (table owner)
--          :n  table name
-- Returns: INDEX_NAME, COLUMNS, UNIQUENESS, INDEX_TYPE, STATUS, VISIBILITY,
--          DISTINCT_KEYS, CLUSTERING_FACTOR, LAST_ANALYZED
select i.index_name,
       (select listagg(c.column_name, ', ')
                 within group (order by c.column_position)
          from all_ind_columns c
         where c.index_owner = i.owner
           and c.index_name = i.index_name) columns,
       i.uniqueness, i.index_type, i.status, i.visibility,
       i.distinct_keys, i.clustering_factor,
       to_char(i.last_analyzed, 'YYYY-MM-DD HH24:MI') last_analyzed
  from all_indexes i
 where i.table_owner = :s
   and i.table_name = upper(:n)
 order by i.index_name
