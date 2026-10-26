BEGIN TRANSACTION;
CREATE TABLE imports (
        source      TEXT PRIMARY KEY,      -- an old config file, copied in once
        imported_at REAL NOT NULL);
CREATE TABLE integrations (
        workspace    TEXT NOT NULL,
        provider     TEXT NOT NULL,
        status       TEXT NOT NULL,          -- connected | syncing | error
        account      TEXT,                   -- e.g. the Google account email
        secret       BLOB,                   -- encrypted JSON (tokens)
        settings     TEXT DEFAULT '{}',      -- JSON (e.g. chosen folder)
        connected_by TEXT,
        connected_at REAL,
        last_sync_at REAL,
        last_error   TEXT,
        doc_count    INTEGER DEFAULT 0,
        PRIMARY KEY (workspace, provider));
INSERT INTO "integrations" VALUES('acme','google_drive','connected','it@acme.com',X'7365616C65642D746F6B656E73','{"folders": [{"id": "f1"}], "access": "everyone"}','admin@acme.com',13.0,14.0,NULL,3);
CREATE TABLE members (
        username      TEXT PRIMARY KEY,    -- the email for single sign-on
        workspace     TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'public'
                      CHECK (role IN ('public', 'hr', 'admin')),
        password_hash TEXT,                -- NULL: single sign-on only
        created_at    REAL NOT NULL,
        last_login_at REAL);
INSERT INTO "members" VALUES('boss','default','Boss','hr','pbkdf2$1$aa$bb',4.0,40.0);
INSERT INTO "members" VALUES('pat@acme.com','acme','Pat','public',NULL,5.0,50.0);
INSERT INTO "members" VALUES('admin@acme.com','acme','Ada','admin',NULL,6.0,NULL);
CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user       TEXT NOT NULL,     -- JSON: username, name, workspace
        expires    REAL NOT NULL);
INSERT INTO "sessions" VALUES('1111111111111111111111111111111111111111111111111111111111111111','{"username": "Pat@Acme.com", "name": "Pat", "workspace": "acme"}',9000000000.0);
INSERT INTO "sessions" VALUES('2222222222222222222222222222222222222222222222222222222222222222','{"username": "boss", "name": "Boss", "workspace": "default"}',9000000000.0);
CREATE TABLE workspace_domains (
        workspace    TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        domain       TEXT NOT NULL,
        verify_token TEXT,                 -- for the DNS check (sign-in settings)
        verified_at  REAL,                 -- NULL until the workspace proves it owns it
        PRIMARY KEY (workspace, domain));
INSERT INTO "workspace_domains" VALUES('acme','acme.com','tok',3.0);
INSERT INTO "workspace_domains" VALUES('default','acme.com',NULL,NULL);
CREATE TABLE workspaces (
        slug        TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  REAL NOT NULL);
INSERT INTO "workspaces" VALUES('default','Default',1.0);
INSERT INTO "workspaces" VALUES('acme','Acme',2.0);
CREATE UNIQUE INDEX one_verified_owner
        ON workspace_domains(domain) WHERE verified_at IS NOT NULL;
COMMIT;
