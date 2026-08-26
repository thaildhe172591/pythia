-- Purpose: what depends on an object — everything a change to it can break.
--          The reverse of dependencies.sql. Status comes from a scalar
--          subquery so the hierarchical row order survives.
-- Binds:   :s      schema (owner of the starting object)
--          :n      starting object name
--          :depth  levels to walk (1 = direct dependents only)
-- Returns: LVL, OWNER, NAME, TYPE, STATUS, DEPENDENCY_TYPE
select level lvl,
       d.owner,
       d.name,
       d.type,
       (select o.status
          from all_objects o
         where o.owner       = d.owner
           and o.object_name = d.name
           and o.object_type = d.type) status,
       d.dependency_type
  from all_dependencies d
 start with d.referenced_owner = :s
        and d.referenced_name  = upper(:n)
connect by nocycle
           prior d.owner = d.referenced_owner
       and prior d.name  = d.referenced_name
       and prior d.type  = d.referenced_type
       and level <= :depth
 order siblings by d.type, d.name
