CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version)
VALUES (1);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    email_address TEXT NOT NULL UNIQUE,
    auth_method TEXT NOT NULL DEFAULT 'password'
        CHECK (auth_method IN ('password', 'oauth2')),
    oauth_provider TEXT
        CHECK (oauth_provider IS NULL OR oauth_provider IN ('gmail', 'outlook')),
    username TEXT,
    imap_host TEXT,
    imap_port INTEGER CHECK (imap_port IS NULL OR imap_port BETWEEN 1 AND 65535),
    imap_security TEXT NOT NULL DEFAULT 'ssl'
        CHECK (imap_security IN ('ssl', 'starttls')),
    smtp_host TEXT,
    smtp_port INTEGER CHECK (smtp_port IS NULL OR smtp_port BETWEEN 1 AND 65535),
    smtp_security TEXT NOT NULL DEFAULT 'starttls'
        CHECK (smtp_security IN ('ssl', 'starttls')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    remote_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE CASCADE,
    UNIQUE (id, account_id),
    UNIQUE (account_id, name)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    folder_id INTEGER NOT NULL,
    imap_uid TEXT,
    flags TEXT NOT NULL DEFAULT '',
    message_id TEXT,
    subject TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    recipients TEXT NOT NULL DEFAULT '',
    sent_at TEXT,
    received_at TEXT,
    is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
    body_preview TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE CASCADE,
    FOREIGN KEY (folder_id) REFERENCES folders (id) ON DELETE CASCADE,
    FOREIGN KEY (folder_id, account_id) REFERENCES folders (id, account_id)
        ON DELETE CASCADE,
    UNIQUE (account_id, folder_id, imap_uid),
    UNIQUE (account_id, folder_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_folders_account_id
ON folders (account_id);

CREATE INDEX IF NOT EXISTS idx_messages_folder_id
ON messages (folder_id);

CREATE INDEX IF NOT EXISTS idx_messages_received_at
ON messages (received_at);
