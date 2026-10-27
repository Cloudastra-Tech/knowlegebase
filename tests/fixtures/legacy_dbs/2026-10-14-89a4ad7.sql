BEGIN TRANSACTION;
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
CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user       TEXT NOT NULL,     -- JSON: username, name, workspace
        expires    REAL NOT NULL);
INSERT INTO "sessions" VALUES('1111111111111111111111111111111111111111111111111111111111111111','{"username": "Pat@Acme.com", "name": "Pat", "workspace": "acme"}',9000000000.0);
INSERT INTO "sessions" VALUES('2222222222222222222222222222222222222222222222222222222222222222','{"username": "boss", "name": "Boss", "workspace": "default"}',9000000000.0);
COMMIT;
