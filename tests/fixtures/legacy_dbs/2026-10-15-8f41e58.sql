BEGIN TRANSACTION;
CREATE TABLE group_role_rules (
        connection_id TEXT NOT NULL REFERENCES sso_connections(id) ON DELETE CASCADE,
        group_name    TEXT NOT NULL,       -- compared without regard to case
        role          TEXT NOT NULL CHECK (role IN ('public', 'hr', 'admin')),
        PRIMARY KEY (connection_id, group_name));
INSERT INTO "group_role_rules" VALUES('c2','eng','hr');
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
        last_login_at REAL, override_role TEXT, group_role TEXT, groups TEXT, disabled INTEGER NOT NULL DEFAULT 0);
INSERT INTO "members" VALUES('boss','default','Boss','hr','pbkdf2$1$aa$bb',4.0,40.0,NULL,NULL,NULL,0);
INSERT INTO "members" VALUES('pat@acme.com','acme','Pat','public',NULL,5.0,50.0,NULL,NULL,'["eng"]',0);
INSERT INTO "members" VALUES('admin@acme.com','acme','Ada','admin',NULL,6.0,NULL,'admin',NULL,NULL,0);
CREATE TABLE saml_assertions (
        assertion_id  TEXT PRIMARY KEY,
        expires       REAL NOT NULL);
INSERT INTO "saml_assertions" VALUES('a1',9000000000.0);
CREATE TABLE saml_requests (
        request_id    TEXT PRIMARY KEY,
        connection_id TEXT NOT NULL REFERENCES sso_connections(id) ON DELETE CASCADE,
        expires       REAL NOT NULL);
INSERT INTO "saml_requests" VALUES('r1','c2',9000000000.0);
CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user       TEXT NOT NULL,     -- JSON: username, name, workspace
        expires    REAL NOT NULL);
INSERT INTO "sessions" VALUES('1111111111111111111111111111111111111111111111111111111111111111','{"username": "Pat@Acme.com", "name": "Pat", "workspace": "acme"}',9000000000.0);
INSERT INTO "sessions" VALUES('2222222222222222222222222222222222222222222222222222222222222222','{"username": "boss", "name": "Boss", "workspace": "default"}',9000000000.0);
CREATE TABLE sso_connections (
        id            TEXT PRIMARY KEY,
        workspace     TEXT NOT NULL REFERENCES workspaces(slug) ON DELETE CASCADE,
        protocol      TEXT NOT NULL CHECK (protocol IN ('oidc', 'saml')),
        label         TEXT NOT NULL,       -- the sign-in button: "Continue with <label>"
        issuer        TEXT,                -- OIDC issuer, or the SAML IdP's entity ID
        client_id     TEXT,                -- OIDC only
        secret        BLOB,                -- encrypted (secret_box.py): the OIDC client
                                           -- secret, or our SAML signing key
        metadata      TEXT NOT NULL DEFAULT '{}',  -- endpoints / IdP metadata, as checked
        enabled       INTEGER NOT NULL DEFAULT 1,
        created_by    TEXT,
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL,
        last_login_at REAL, group_claim TEXT);
INSERT INTO "sso_connections" VALUES('c1','acme','oidc','Okta','https://idp.example','kb',X'7365616C65642D6F696463','{}',1,'admin@acme.com',7.0,7.0,100.0,NULL);
INSERT INTO "sso_connections" VALUES('c2','acme','saml','Entra','https://sts.example',NULL,X'7365616C65642D73616D6C','{"sso_url": "https://sts.example/sso"}',1,'admin@acme.com',8.0,9.0,200.0,NULL);
CREATE TABLE sso_events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace     TEXT NOT NULL,
        connection_id TEXT,
        email         TEXT,
        outcome       TEXT NOT NULL
                      CHECK (outcome IN ('signed_in', 'refused', 'failed', 'test')),
        reason        TEXT,                -- why it was refused or failed
        at            REAL NOT NULL);
INSERT INTO "sso_events" VALUES(1,'acme','c1','pat@acme.com','signed_in',NULL,10.0);
INSERT INTO "sso_events" VALUES(2,'acme','c2','eve@evil.example','refused','domain_not_allowed',11.0);
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
CREATE INDEX sso_events_by_workspace ON sso_events(workspace, id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('sso_events',2);
COMMIT;
