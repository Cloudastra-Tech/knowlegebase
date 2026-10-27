"""
migrations/postgres.py  —  what a Postgres database has besides its tables:
row-level security for every tenant table (platform.md PL-04 step 3), run by
env.py after every upgrade, so a table a later migration adds is covered too.

    the tenant role    DB_TENANT_ROLE (default kb_tenant), NOLOGIN, without
                       BYPASSRLS. The login role is made a member of it (SET only,
                       no inherited rights) so db_pg can switch a tenant's
                       connections to it. It may read and write every table;
                       the policies decide which rows.
    the policies       a table with a `workspace` column: its rows whose
                       workspace is current_setting('app.workspace'); workspaces:
                       by slug; a table that only points at one (messages,
                       scim_group_members, ...): the rows whose parent row the
                       tenant can see. Without app.workspace set, a tenant sees
                       nothing and can write nothing.
    the platform role  the login role. It owns the tables, so the policies don't
                       limit it (they are ENABLEd, not FORCEd): jobs of no
                       workspace, the operator, sign-in before the workspace is
                       known, and these migrations. Give it BYPASSRLS too if the
                       tables are owned by another role (deploy.md).

Making a role needs CREATEROLE. Without it, nothing here is applied, a warning
says so, and connections run as the login role (as with DB_RLS=off).
"""

import logging

import db_pg

log = logging.getLogger("migrations")

POLICY = "kb_tenant_isolation"
SETTING = "current_setting('app.workspace', true)"
# Tables without a workspace column that belong to one through their parent:
# table -> (its column, the parent table, the parent's column).
# (Not slack_threads / teams_threads: they route a Slack or Teams thread, keyed by
# that service's ids, to a conversation, and a team or tenant can move to another
# workspace; the conversation they point at is the tenant's, and secured.)
CHILDREN = {
    "messages": ("conversation_id", "conversations", "id"),
    "conversation_search": ("conversation_id", "conversations", "id"),
    "scim_group_members": ("group_id", "scim_groups", "id"),
    "saml_requests": ("connection_id", "sso_connections", "id"),
    "group_role_rules": ("connection_id", "sso_connections", "id"),
    "verified_answer_vectors": ("answer_id", "verified_answers", "id"),
}


# What the stores' SQL takes for granted from SQLite, where a boolean is an
# integer: SUM(a = b) counts the rows it's true for (NULL for no rows, as
# SQLite's). Made (again) after every upgrade.
COMPAT = (
    """CREATE OR REPLACE FUNCTION kb_sum_bool(bigint, boolean) RETURNS bigint
       LANGUAGE sql IMMUTABLE AS
       'SELECT COALESCE($1, 0) + CASE WHEN $2 THEN 1 ELSE 0 END'""",
    """DO $$ BEGIN
           IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_aggregate a ON a.aggfnoid = p.oid
                           WHERE p.proname = 'sum' AND p.pronamespace = current_schema()::regnamespace
                             AND p.proargtypes = '16') THEN
               CREATE AGGREGATE sum(boolean) (SFUNC = kb_sum_bool, STYPE = bigint);
           END IF;
       END $$""",
)


def compat(conn):
    for statement in COMPAT:
        conn.exec_driver_sql(statement)


def _columns(conn):
    out = {}
    for table, column in conn.exec_driver_sql(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c "
            "JOIN information_schema.tables t ON t.table_name = c.table_name "
            "AND t.table_schema = c.table_schema WHERE c.table_schema = current_schema() "
            "AND t.table_type = 'BASE TABLE'"):
        out.setdefault(table, set()).add(column)
    return out


def ensure_role(conn, role=None):
    """The tenant role exists and the login role may SET ROLE to it. False if it
    can't be made (no CREATEROLE)."""
    role = role or db_pg.TENANT_ROLE
    savepoint = conn.begin_nested()
    try:
        if not conn.exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname = %(r)s",
                                    {"r": role}).fetchone():
            conn.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS')
        member = conn.exec_driver_sql("SELECT pg_has_role(current_user, %(r)s, 'SET')",
                                      {"r": role}).scalar()
        if not member:
            new = int(conn.exec_driver_sql("SHOW server_version_num").scalar()) >= 160000
            conn.exec_driver_sql(f'GRANT "{role}" TO CURRENT_USER'
                                 + (" WITH INHERIT FALSE, SET TRUE" if new else ""))
        savepoint.commit()
        return True
    except Exception as e:                              # no CREATEROLE: no row security
        savepoint.rollback()
        log.warning("Row-level security not set up: the database user can't make the role "
                    "%s (%s). Tenant connections run as the login role.", role,
                    str(e).splitlines()[0])
        return False


def secure_table(conn, table, columns, role=None):
    """Give one table its tenant policy (again), if it is a tenant table."""
    if "workspace" in columns:
        using = f"workspace = {SETTING}"
    elif table == "workspaces":
        using = f"slug = {SETTING}"
    elif table in CHILDREN:
        column, parent, key = CHILDREN[table]
        using = f"EXISTS (SELECT 1 FROM {parent} p WHERE p.{key} = {table}.{column})"
    else:
        return False
    # Already there (every start runs this): nothing is locked or changed. A new
    # rule for a table needs a new POLICY name.
    if conn.exec_driver_sql(
            "SELECT 1 FROM pg_class c JOIN pg_policies p ON p.tablename = c.relname "
            "AND p.schemaname = current_schema() WHERE c.oid = to_regclass(%(t)s) "
            "AND c.relrowsecurity AND p.policyname = %(p)s",
            {"t": f'"{table}"', "p": POLICY}).fetchone():
        return True
    conn.exec_driver_sql(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    conn.exec_driver_sql(f'DROP POLICY IF EXISTS {POLICY} ON "{table}"')
    conn.exec_driver_sql(f'CREATE POLICY {POLICY} ON "{table}" USING ({using}) '
                         f'WITH CHECK ({using})')
    return True


def secure(conn, role=None):
    """Row-level security on every tenant table, and the tenant role's grants.
    Returns the tables with a policy (none when the role can't be made)."""
    role = role or db_pg.TENANT_ROLE
    if not ensure_role(conn, role):
        return []
    conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{_schema(conn)}" TO "{role}"')
    conn.exec_driver_sql(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA '
                         f'"{_schema(conn)}" TO "{role}"')
    conn.exec_driver_sql(f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA '
                         f'"{_schema(conn)}" TO "{role}"')
    conn.exec_driver_sql(f'REVOKE ALL ON alembic_version FROM "{role}"')
    done = []
    for table, columns in sorted(_columns(conn).items()):
        if table != "alembic_version" and secure_table(conn, table, columns, role):
            done.append(table)
    return done


def _schema(conn):
    return conn.exec_driver_sql("SELECT current_schema()").scalar()
