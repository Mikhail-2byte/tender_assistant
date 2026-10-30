-- UTF-8. Дата/время — исходные календарные значения; часовой пояс неизвестен.
PRAGMA foreign_keys = ON;
CREATE TABLE tenders (
 id INTEGER PRIMARY KEY,
 number TEXT NOT NULL DEFAULT '',
 customer TEXT NOT NULL DEFAULT '',
 title TEXT NOT NULL DEFAULT '',
 url TEXT NOT NULL DEFAULT '',
 platform TEXT NOT NULL DEFAULT '',
 deadline_date TEXT,
 deadline_time TEXT,
 deadline_timezone TEXT,
 deadline_raw TEXT,
 deadline_needs_review INTEGER NOT NULL DEFAULT 0 CHECK(deadline_needs_review IN (0,1)),
 comment TEXT NOT NULL DEFAULT '',
 action TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT '',
 contacts TEXT NOT NULL DEFAULT '',
 folder_path TEXT NOT NULL DEFAULT '',
 original_folder_link TEXT NOT NULL DEFAULT '',
 original_url_cell TEXT NOT NULL DEFAULT '',
 import_issues TEXT NOT NULL DEFAULT '',
 source_sheet TEXT NOT NULL,
 source_row INTEGER NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX idx_tenders_number ON tenders(number);
CREATE INDEX idx_tenders_status_deadline ON tenders(status, deadline_date, deadline_time);
CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
PRAGMA user_version = 1;
