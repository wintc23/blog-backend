-- AI digest schema reference, 2026-09-16. Applied via Alembic 20260916_ai_digest.
-- Reference only: use the migration, do not execute this file on an existing database.
-- UTC DATETIME; issue_date uses the configured publication timezone.
-- Validate JSON documents and status transitions in the application.

CREATE TABLE ai_digest_settings (
  id INT NOT NULL PRIMARY KEY COMMENT 'Singleton: application enforces id=1',
  title VARCHAR(128) NOT NULL,
  timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
  publish_time TIME NOT NULL DEFAULT '09:00:00',
  auto_publish BOOLEAN NOT NULL DEFAULT FALSE,
  email_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  lookback_hours INT NOT NULL DEFAULT 24,
  max_lookback_hours INT NOT NULL DEFAULT 72,
  target_min_chars INT NOT NULL DEFAULT 1000,
  target_max_chars INT NOT NULL DEFAULT 1500,
  preferences_json MEDIUMTEXT NOT NULL COMMENT 'Topics, selection rules, image policy',
  generator_json MEDIUMTEXT NOT NULL COMMENT 'Provider/model/credential_ref; no secrets',
  prompt_template MEDIUMTEXT NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ai_news_sources (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  kind VARCHAR(16) NOT NULL COMMENT 'rss / api / webpage',
  endpoint_url TEXT NOT NULL,
  endpoint_hash BINARY(32) NOT NULL,
  homepage_url TEXT,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  priority INT NOT NULL DEFAULT 0,
  config_json MEDIUMTEXT NOT NULL COMMENT 'Allowlisted parser, fetch rules, rights policy',
  etag VARCHAR(512),
  last_modified VARCHAR(128),
  last_success_at DATETIME,
  last_error VARCHAR(1000),
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_ai_source_endpoint (endpoint_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ai_news_items (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  source_id INT NOT NULL,
  external_id VARCHAR(512),
  canonical_url TEXT NOT NULL,
  url_hash BINARY(32) NOT NULL,
  title VARCHAR(512) NOT NULL,
  published_date DATE,
  published_at DATETIME,
  date_precision VARCHAR(16) NOT NULL DEFAULT 'unknown' COMMENT 'unknown / date / timestamp',
  source_updated_at DATETIME,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  language VARCHAR(16),
  event_key VARCHAR(191) COMMENT 'Event clustering key; not unique across sources',
  evidence_json MEDIUMTEXT NOT NULL COMMENT 'Bounded source excerpts, extracted facts, image metadata',
  content_hash BINARY(32) NOT NULL,
  selection_status VARCHAR(16) NOT NULL DEFAULT 'pending',
  skip_reason VARCHAR(500),
  UNIQUE KEY uq_ai_item_url (url_hash),
  KEY ix_ai_item_source_date (source_id, published_date),
  KEY ix_ai_item_selection_seen (selection_status, first_seen_at),
  KEY ix_ai_item_event (event_key),
  CONSTRAINT fk_ai_item_source FOREIGN KEY (source_id) REFERENCES ai_news_sources (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ai_digests (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  issue_date DATE NOT NULL,
  timezone VARCHAR(64) NOT NULL,
  slug VARCHAR(191) NOT NULL,
  title VARCHAR(255) NOT NULL,
  summary TEXT NOT NULL,
  content_json MEDIUMTEXT NOT NULL COMMENT 'Versioned renderable article with source snapshots',
  content_version INT NOT NULL DEFAULT 1,
  status VARCHAR(16) NOT NULL DEFAULT 'draft' COMMENT 'draft / ready / published / withdrawn',
  source_window_start DATETIME NOT NULL,
  source_window_end DATETIME NOT NULL,
  mail_snapshot_json MEDIUMTEXT COMMENT 'Frozen when recipients are enqueued',
  mail_content_version INT,
  scheduled_publish_at DATETIME NOT NULL COMMENT 'Planned edition time in UTC',
  published_at DATETIME,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_ai_digest_date (issue_date),
  UNIQUE KEY uq_ai_digest_slug (slug),
  KEY ix_ai_digest_public (status, issue_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ai_digest_items (
  digest_id INT NOT NULL,
  content_version INT NOT NULL,
  item_id INT NOT NULL,
  block_id VARCHAR(64) NOT NULL,
  position INT NOT NULL DEFAULT 0,
  evidence_snapshot_json MEDIUMTEXT NOT NULL COMMENT 'Facts used for this revision; preserved on source updates',
  PRIMARY KEY (digest_id, content_version, block_id, item_id),
  KEY ix_ai_digest_item (item_id),
  CONSTRAINT fk_ai_di_digest FOREIGN KEY (digest_id) REFERENCES ai_digests (id),
  CONSTRAINT fk_ai_di_item FOREIGN KEY (item_id) REFERENCES ai_news_items (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ai_digest_runs (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  run_key VARCHAR(64) NOT NULL,
  digest_id INT,
  issue_date DATE NOT NULL,
  attempt INT NOT NULL DEFAULT 1,
  trigger_kind VARCHAR(16) NOT NULL COMMENT 'scheduled / manual / retry / backfill',
  stage VARCHAR(16) NOT NULL COMMENT 'collect / select / generate / validate / publish / enqueue',
  status VARCHAR(16) NOT NULL COMMENT 'running / succeeded / failed / skipped',
  lock_token VARCHAR(64),
  lease_until DATETIME,
  settings_snapshot_json MEDIUMTEXT NOT NULL,
  result_json MEDIUMTEXT COMMENT 'Validation report, candidate ids, model usage; no secrets',
  error_code VARCHAR(64),
  error_message VARCHAR(1000),
  started_at DATETIME NOT NULL,
  finished_at DATETIME,
  UNIQUE KEY uq_ai_run_key (run_key),
  KEY ix_ai_run_date (issue_date, started_at),
  KEY ix_ai_run_recovery (status, lease_until),
  CONSTRAINT fk_ai_run_digest FOREIGN KEY (digest_id) REFERENCES ai_digests (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE newsletter_subscribers (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(254) NOT NULL,
  email_normalized VARCHAR(254) NOT NULL,
  email_hash BINARY(32) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'pending / active / unsubscribed / bounced',
  subscription_version INT NOT NULL DEFAULT 1,
  confirm_token_hash BINARY(32),
  confirm_expires_at DATETIME,
  confirmation_sent_at DATETIME,
  unsubscribe_token_hash BINARY(32),
  signup_origin VARCHAR(64) NOT NULL,
  consent_version VARCHAR(32) NOT NULL,
  confirmed_at DATETIME,
  unsubscribed_at DATETIME,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_newsletter_email (email_hash),
  UNIQUE KEY uq_newsletter_confirm_token (confirm_token_hash),
  UNIQUE KEY uq_newsletter_unsubscribe_token (unsubscribe_token_hash),
  KEY ix_newsletter_active (status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE newsletter_deliveries (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  digest_id INT NOT NULL,
  subscriber_id INT NOT NULL,
  subscription_version INT NOT NULL,
  content_version INT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'queued',
  attempt_count INT NOT NULL DEFAULT 0,
  next_attempt_at DATETIME NOT NULL,
  lock_token VARCHAR(64),
  lease_until DATETIME,
  message_id VARCHAR(191) NOT NULL,
  provider_message_id VARCHAR(191),
  last_error_code VARCHAR(64),
  last_error_message VARCHAR(1000),
  accepted_at DATETIME,
  delivered_at DATETIME,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_newsletter_delivery (digest_id, subscriber_id),
  UNIQUE KEY uq_newsletter_message (message_id),
  KEY ix_newsletter_queue (status, next_attempt_at),
  KEY ix_newsletter_delivery_subscriber (subscriber_id),
  CONSTRAINT fk_newsletter_delivery_digest FOREIGN KEY (digest_id) REFERENCES ai_digests (id),
  CONSTRAINT fk_newsletter_delivery_subscriber FOREIGN KEY (subscriber_id) REFERENCES newsletter_subscribers (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
