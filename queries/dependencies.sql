-- Purpose: what an object depends on, walked downward. NOCYCLE survives
--          circular references; the depth bound sits in CONNECT BY so the
--          walk is pruned rather than filtered after the fact.
--          Oracle's own built-ins (SYS.STANDARD and friends) are excluded
--          unless asked for: every PL/SQL object depends on them, so they
--          crowd out the dependencies a developer is actually looking for.
--          The test is applied in both START WITH and CONNECT BY, so an
--          excluded object takes its whole subtree with it instead of
--          leaving its children behind as orphans.
-- Binds:   :s        schema (owner of the starting object)
--          :n        starting object name
--          :depth    levels to walk (1 = direct dependencies only)
--          :with_sys 1 to include SYS/PUBLIC built-ins, 0 to leave them out
-- Returns: LVL, OWNER, NAME, TYPE, DEPENDENCY_TYPE
select level lvl,
       d.referenced_owner owner,
       d.referenced_name  name,
       d.referenced_type  type,
       d.dependency_type
  from all_dependencies d
 start with d.owner = :s
        and d.name  = upper(:n)
        and (:with_sys = 1 or d.referenced_owner not in ('SYS', 'PUBLIC'))
connect by nocycle
           prior d.referenced_owner = d.owner
       and prior d.referenced_name  = d.name
       and prior d.referenced_type  = d.type
       and level <= :depth
       and (:with_sys = 1 or d.referenced_owner not in ('SYS', 'PUBLIC'))
 order siblings by d.referenced_type, d.referenced_name
