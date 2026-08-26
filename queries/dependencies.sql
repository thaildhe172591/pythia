-- Purpose: what an object depends on, walked downward. NOCYCLE survives
--          circular references; the depth bound sits in CONNECT BY so the
--          walk is pruned rather than filtered after the fact.
-- Binds:   :s      schema (owner of the starting object)
--          :n      starting object name
--          :depth  levels to walk (1 = direct dependencies only)
-- Returns: LVL, OWNER, NAME, TYPE, DEPENDENCY_TYPE
select level lvl,
       d.referenced_owner owner,
       d.referenced_name  name,
       d.referenced_type  type,
       d.dependency_type
  from all_dependencies d
 start with d.owner = :s
        and d.name  = upper(:n)
connect by nocycle
           prior d.referenced_owner = d.owner
       and prior d.referenced_name  = d.name
       and prior d.referenced_type  = d.type
       and level <= :depth
 order siblings by d.referenced_type, d.referenced_name
