-- Database initialisation for the DNS Zone Manager example environment.
--
-- Runs once, the first time the postgres container initialises its data
-- directory. The role and database themselves are created by the image from
-- POSTGRES_USER / POSTGRES_DB, so this only sets up privileges.
--
-- Note what is deliberately absent: there is no table DDL here. The
-- application creates its own schema on first start (database.auto_migrate),
-- so this example demonstrates pointing DNS Zone Manager at a blank database.

-- Let the application own objects it creates in the public schema.
GRANT ALL ON SCHEMA public TO dns_zone_manager;
ALTER SCHEMA public OWNER TO dns_zone_manager;

-- Readable timestamps when poking around with psql.
ALTER DATABASE dns_zone_manager SET timezone TO 'UTC';
