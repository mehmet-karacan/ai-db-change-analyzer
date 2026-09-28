PRAGMA foreign_keys = ON;

CREATE TABLE installation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    installation_uuid TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE scopes (
    scope_hash TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    remote_digest TEXT NOT NULL,
    branch TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    epoch INTEGER NOT NULL DEFAULT 1 CHECK (epoch >= 1),
    checkpoint_sha TEXT,
    initialized_at TEXT NOT NULL
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    scope_hash TEXT NOT NULL REFERENCES scopes(scope_hash),
    mode TEXT NOT NULL,
    base_sha TEXT,
    target_sha TEXT,
    epoch INTEGER NOT NULL,
    analysis_generation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    quality TEXT,
    config_digest TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    originating_build_json TEXT,
    planned_at TEXT NOT NULL,
    last_attempt_at TEXT,
    report_id TEXT
);

CREATE UNIQUE INDEX one_unfinished_auto_run
ON runs(scope_hash, epoch)
WHERE mode = 'AUTO' AND status NOT IN ('BASELINED', 'NO_CHANGE', 'OUT_OF_SCOPE_ONLY', 'COMMITTED', 'CLOSED');

CREATE UNIQUE INDEX completed_auto_range
ON runs(scope_hash, epoch, base_sha, target_sha, analysis_generation)
WHERE mode = 'AUTO' AND status = 'COMMITTED';

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    commit_sha TEXT NOT NULL,
    parent_sha TEXT,
    object_key TEXT NOT NULL,
    operation TEXT NOT NULL,
    before_occurrence TEXT,
    after_occurrence TEXT,
    view_tags TEXT NOT NULL,
    ordinal INTEGER NOT NULL
);

CREATE TABLE units (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    analysis_generation INTEGER NOT NULL,
    unit_id TEXT NOT NULL,
    canonical_sources_json TEXT NOT NULL,
    alias_events_json TEXT NOT NULL,
    context_digest TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    diagnostics_json TEXT NOT NULL,
    http_attempts INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    PRIMARY KEY (run_id, analysis_generation, unit_id)
);

CREATE TABLE reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    canonical_json BLOB NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    html BLOB NOT NULL,
    text BLOB NOT NULL,
    rendered_version TEXT NOT NULL,
    quality TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE notifications (
    notification_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES reports(report_id),
    generation INTEGER NOT NULL CHECK (generation >= 0),
    envelope_json TEXT NOT NULL,
    message_id TEXT NOT NULL,
    mime_bytes BLOB NOT NULL,
    mime_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    UNIQUE (report_id, generation)
);

CREATE TABLE notification_recipients (
    notification_id TEXT NOT NULL REFERENCES notifications(notification_id) ON DELETE CASCADE,
    address TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'ACCEPTED', 'REFUSED', 'UNKNOWN')),
    smtp_code INTEGER,
    accepted_at TEXT,
    PRIMARY KEY (notification_id, address)
);

CREATE TABLE notification_attempts (
    attempt_id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL REFERENCES notifications(notification_id) ON DELETE CASCADE,
    intended_recipients_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('INFLIGHT', 'ACCEPTED', 'FAILED', 'UNKNOWN')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    sanitized_error TEXT
);

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_hash TEXT,
    run_id TEXT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    expected_base TEXT,
    target TEXT,
    reason TEXT,
    evidence_reference TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE cache_entries (
    cache_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    version_fingerprint TEXT NOT NULL,
    validated_content BLOB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    accessed_at TEXT NOT NULL
);

PRAGMA user_version = 1;
