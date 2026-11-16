-- Purpose: the synonyms that point straight at an object — other names a
--          caller can reach it by, which neither ALL_DEPENDENCIES' tree nor
--          a grep for the object's own name will show. Direct synonyms
--          only: a synonym of a synonym is not followed.
-- Binds:   :s  schema (owner of the object)
--          :n  object name
-- Returns: OWNER, SYNONYM_NAME (PUBLIC for a public synonym)
select s.owner,
       s.synonym_name
  from all_synonyms s
 where s.table_owner = :s
   and s.table_name  = upper(:n)
 order by case s.owner when 'PUBLIC' then 0 else 1 end, s.owner, s.synonym_name
