-- Purpose: the development environment of one schema at a glance: how many
--          PL/SQL units exist, how many carry PL/Scope identifiers (exact
--          usage answers) and compiler warnings, what is INVALID, and whether
--          the utPLSQL framework is reachable for unit tests.
-- Binds:   :s  schema (object owner)
-- Returns: UNITS, PLSCOPE_UNITS, WARNING_UNITS, INVALID_OBJECTS, UTPLSQL
select (select count(*) from all_plsql_object_settings
         where owner = :s) units,
       (select count(*) from all_plsql_object_settings
         where owner = :s
           and plscope_settings like '%IDENTIFIERS:ALL%') plscope_units,
       (select count(*) from all_plsql_object_settings
         where owner = :s
           and plsql_warnings not like 'DISABLE:ALL%') warning_units,
       (select count(*) from all_objects
         where owner = :s and status = 'INVALID') invalid_objects,
       (select count(*) from all_objects
         where object_name = 'UT' and object_type = 'PACKAGE') utplsql
  from dual
