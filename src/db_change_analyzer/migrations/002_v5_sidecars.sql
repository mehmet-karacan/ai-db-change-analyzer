BEGIN IMMEDIATE;

ALTER TABLE units ADD COLUMN returned_models_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE runs ADD COLUMN ai_phase_started_at TEXT;
ALTER TABLE runs ADD COLUMN analysis_started_at TEXT;

CREATE TABLE report_render_sidecars (
    report_id TEXT NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    render_generation INTEGER NOT NULL CHECK (render_generation >= 0),
    source_report_sha256 TEXT NOT NULL,
    mail_view_json BLOB NOT NULL,
    mail_view_sha256 TEXT NOT NULL,
    manifest_json BLOB NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    html BLOB NOT NULL,
    text BLOB NOT NULL,
    mime BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (report_id, render_generation)
);

UPDATE installation SET schema_version = 2 WHERE singleton = 1 AND schema_version = 1;
PRAGMA user_version = 2;

COMMIT;
