-- Read-only gate before replacing the server. No user data or names are printed.
\set ON_ERROR_STOP on
BEGIN READ ONLY;
DO $score_it$
DECLARE
    affected bigint;
BEGIN
    WITH RECURSIVE xml_types(oid) AS (
        SELECT 'pg_catalog.xml'::regtype::oid
        UNION
        SELECT t.oid FROM pg_type t JOIN xml_types x
          ON t.typbasetype=x.oid OR t.typelem=x.oid
    )
    SELECT count(*) INTO affected
      FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE a.attnum>0 AND NOT a.attisdropped
        AND n.nspname NOT IN ('pg_catalog','information_schema')
        AND n.nspname NOT LIKE 'pg_toast%'
        AND a.atttypid IN (SELECT oid FROM xml_types);
    IF affected<>0 THEN
        RAISE EXCEPTION 'POSTGRES_PREFLIGHT_BLOCKED: XML columns or views exist';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname='xml2') THEN
        RAISE EXCEPTION 'POSTGRES_PREFLIGHT_BLOCKED: xml2 extension exists';
    END IF;
    -- pg_depend covers parsed view/function expressions. The source check also
    -- rejects unparsed function bodies that could call XML dynamically.
    IF EXISTS (
        SELECT 1 FROM pg_depend d JOIN pg_proc p ON p.oid=d.refobjid
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE d.refclassid='pg_proc'::regclass AND n.nspname='pg_catalog'
          AND (p.prorettype='xml'::regtype OR 'xml'::regtype=ANY(p.proargtypes::oid[]))
          AND d.deptype<>'i'
    ) OR EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema')
          AND p.prosrc ~* '\m(xmlparse|xmlserialize|xmlexists|xmltable|xpath|xml_is_well_formed)\M'
    ) THEN
        RAISE EXCEPTION 'POSTGRES_PREFLIGHT_BLOCKED: XML function dependencies need review';
    END IF;
END
$score_it$;
COMMIT;
\echo POSTGRES_FEATURE_PREFLIGHT_OK
