-- Purpose: what the optimizer believes about one table — rows, blocks, when
--          it last looked, and whether that is stale. Tuning starts from
--          these numbers, not from a guess about the data.
-- Binds:   :s  schema (table owner)
--          :n  table name
-- Returns: TABLE_NAME, NUM_ROWS, BLOCKS, AVG_ROW_LEN, LAST_ANALYZED,
--          STALE_STATS, PARTITIONED, TEMPORARY, IOT_TYPE
select t.table_name, t.num_rows, t.blocks, t.avg_row_len,
       to_char(t.last_analyzed, 'YYYY-MM-DD HH24:MI') last_analyzed,
       s.stale_stats, t.partitioned, t.temporary, t.iot_type
  from all_tables t
  left join all_tab_statistics s
    on s.owner = t.owner
   and s.table_name = t.table_name
   and s.object_type = 'TABLE'
 where t.owner = :s
   and t.table_name = upper(:n)
