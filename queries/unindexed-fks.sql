-- Purpose: foreign keys on one table whose columns lead no index. Deleting or
--          updating the parent key then locks the whole child table — a
--          classic cause of "the system hangs when someone deletes".
-- Binds:   :s  schema (table owner)
--          :n  table name
-- Returns: CONSTRAINT_NAME, COLUMNS, PARENT_TABLE
select c.constraint_name,
       (select listagg(cc.column_name, ', ')
                 within group (order by cc.position)
          from all_cons_columns cc
         where cc.owner = c.owner
           and cc.constraint_name = c.constraint_name) columns,
       (select p.table_name
          from all_constraints p
         where p.owner = c.r_owner
           and p.constraint_name = c.r_constraint_name) parent_table
  from all_constraints c
 where c.owner = :s
   and c.table_name = upper(:n)
   and c.constraint_type = 'R'
   and not exists (
         select 1
           from all_ind_columns ic
           join all_cons_columns cc
             on cc.owner = c.owner
            and cc.constraint_name = c.constraint_name
            and cc.column_name = ic.column_name
            and cc.position = ic.column_position
          where ic.table_owner = c.owner
            and ic.table_name = c.table_name
          group by ic.index_owner, ic.index_name
         having count(*) = (select count(*)
                              from all_cons_columns x
                             where x.owner = c.owner
                               and x.constraint_name = c.constraint_name))
 order by c.constraint_name
