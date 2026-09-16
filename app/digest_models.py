"""Persistent AI digest, provenance and newsletter records."""
from . import db
from sqlalchemy.dialects.mysql import MEDIUMTEXT


class AiDigestSettings(db.Model):
    __tablename__ = 'ai_digest_settings'
    id = db.Column(db.Integer(), primary_key=True, autoincrement=False, nullable=False)
    title = db.Column(db.String(128), nullable=False)
    timezone = db.Column(db.String(64), nullable=False, server_default='Asia/Shanghai')
    publish_time = db.Column(db.Time(), nullable=False, server_default='09:00:00')
    auto_publish = db.Column(db.Boolean(), nullable=False, server_default='0')
    email_enabled = db.Column(db.Boolean(), nullable=False, server_default='0')
    lookback_hours = db.Column(db.Integer(), nullable=False, server_default='24')
    max_lookback_hours = db.Column(db.Integer(), nullable=False, server_default='72')
    target_min_chars = db.Column(db.Integer(), nullable=False, server_default='1000')
    target_max_chars = db.Column(db.Integer(), nullable=False, server_default='1500')
    preferences_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    generator_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    prompt_template = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    updated_at = db.Column(db.DateTime(), nullable=False)


class AiNewsSource(db.Model):
    __tablename__ = 'ai_news_sources'
    id = db.Column(db.Integer(), primary_key=True, autoincrement=True, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    endpoint_url = db.Column(db.Text(), nullable=False)
    endpoint_hash = db.Column(db.BINARY(32), nullable=False)
    homepage_url = db.Column(db.Text())
    enabled = db.Column(db.Boolean(), nullable=False, server_default='1')
    priority = db.Column(db.Integer(), nullable=False, server_default='0')
    config_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    etag = db.Column(db.String(512))
    last_modified = db.Column(db.String(128))
    last_success_at = db.Column(db.DateTime())
    last_error = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime(), nullable=False)
    updated_at = db.Column(db.DateTime(), nullable=False)
    __table_args__ = (
        db.UniqueConstraint('endpoint_hash', name='uq_ai_source_endpoint'),
    )


class AiNewsItem(db.Model):
    __tablename__ = 'ai_news_items'
    id = db.Column(db.Integer(), primary_key=True, autoincrement=True, nullable=False)
    source_id = db.Column(db.Integer(), nullable=False)
    external_id = db.Column(db.String(512))
    canonical_url = db.Column(db.Text(), nullable=False)
    url_hash = db.Column(db.BINARY(32), nullable=False)
    title = db.Column(db.String(512), nullable=False)
    published_date = db.Column(db.Date())
    published_at = db.Column(db.DateTime())
    date_precision = db.Column(db.String(16), nullable=False, server_default='unknown')
    source_updated_at = db.Column(db.DateTime())
    first_seen_at = db.Column(db.DateTime(), nullable=False)
    last_seen_at = db.Column(db.DateTime(), nullable=False)
    language = db.Column(db.String(16))
    event_key = db.Column(db.String(191))
    evidence_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    content_hash = db.Column(db.BINARY(32), nullable=False)
    selection_status = db.Column(db.String(16), nullable=False, server_default='pending')
    skip_reason = db.Column(db.String(500))
    __table_args__ = (
        db.UniqueConstraint('url_hash', name='uq_ai_item_url'),
        db.Index('ix_ai_item_source_date', 'source_id', 'published_date'),
        db.Index('ix_ai_item_selection_seen', 'selection_status', 'first_seen_at'),
        db.Index('ix_ai_item_event', 'event_key'),
        db.ForeignKeyConstraint(['source_id'], ['ai_news_sources.id'], name='fk_ai_item_source'),
    )


class AiDigest(db.Model):
    __tablename__ = 'ai_digests'
    id = db.Column(db.Integer(), primary_key=True, autoincrement=True, nullable=False)
    issue_date = db.Column(db.Date(), nullable=False)
    timezone = db.Column(db.String(64), nullable=False)
    slug = db.Column(db.String(191), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    summary = db.Column(db.Text(), nullable=False)
    content_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    content_version = db.Column(db.Integer(), nullable=False, server_default='1')
    read_times = db.Column(db.BigInteger(), nullable=False, default=0, server_default='0')
    status = db.Column(db.String(16), nullable=False, server_default='draft')
    source_window_start = db.Column(db.DateTime(), nullable=False)
    source_window_end = db.Column(db.DateTime(), nullable=False)
    mail_snapshot_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"))
    mail_content_version = db.Column(db.Integer())
    scheduled_publish_at = db.Column(db.DateTime(), nullable=False)
    published_at = db.Column(db.DateTime())
    created_at = db.Column(db.DateTime(), nullable=False)
    updated_at = db.Column(db.DateTime(), nullable=False)
    __table_args__ = (
        db.UniqueConstraint('issue_date', name='uq_ai_digest_date'),
        db.UniqueConstraint('slug', name='uq_ai_digest_slug'),
        db.Index('ix_ai_digest_public', 'status', 'issue_date'),
    )


class AiDigestItem(db.Model):
    __tablename__ = 'ai_digest_items'
    digest_id = db.Column(db.Integer(), nullable=False)
    content_version = db.Column(db.Integer(), nullable=False)
    item_id = db.Column(db.Integer(), nullable=False)
    block_id = db.Column(db.String(64), nullable=False)
    position = db.Column(db.Integer(), nullable=False, server_default='0')
    evidence_snapshot_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    __table_args__ = (
        db.PrimaryKeyConstraint('digest_id', 'content_version', 'block_id', 'item_id'),
        db.Index('ix_ai_digest_item', 'item_id'),
        db.ForeignKeyConstraint(['digest_id'], ['ai_digests.id'], name='fk_ai_di_digest'),
        db.ForeignKeyConstraint(['item_id'], ['ai_news_items.id'], name='fk_ai_di_item'),
    )


class AiDigestRun(db.Model):
    __tablename__ = 'ai_digest_runs'
    id = db.Column(db.Integer(), primary_key=True, autoincrement=True, nullable=False)
    run_key = db.Column(db.String(64), nullable=False)
    digest_id = db.Column(db.Integer())
    issue_date = db.Column(db.Date(), nullable=False)
    attempt = db.Column(db.Integer(), nullable=False, server_default='1')
    trigger_kind = db.Column(db.String(16), nullable=False)
    stage = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(16), nullable=False)
    lock_token = db.Column(db.String(64))
    lease_until = db.Column(db.DateTime())
    settings_snapshot_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    result_json = db.Column(db.Text().with_variant(MEDIUMTEXT(), "mysql"))
    error_code = db.Column(db.String(64))
    error_message = db.Column(db.String(1000))
    started_at = db.Column(db.DateTime(), nullable=False)
    finished_at = db.Column(db.DateTime())
    __table_args__ = (
        db.UniqueConstraint('run_key', name='uq_ai_run_key'),
        db.Index('ix_ai_run_date', 'issue_date', 'started_at'),
        db.Index('ix_ai_run_recovery', 'status', 'lease_until'),
        db.ForeignKeyConstraint(['digest_id'], ['ai_digests.id'], name='fk_ai_run_digest'),
    )


class NewsletterSubscriber(db.Model):
    __tablename__ = 'newsletter_subscribers'
    id = db.Column(db.Integer(), primary_key=True, autoincrement=True, nullable=False)
    email = db.Column(db.String(254), nullable=False)
    email_normalized = db.Column(db.String(254), nullable=False)
    email_hash = db.Column(db.BINARY(32), nullable=False)
    status = db.Column(db.String(16), nullable=False, server_default='pending')
    subscription_version = db.Column(db.Integer(), nullable=False, server_default='1')
    confirm_token_hash = db.Column(db.BINARY(32))
    confirm_expires_at = db.Column(db.DateTime())
    confirmation_sent_at = db.Column(db.DateTime())
    unsubscribe_token_hash = db.Column(db.BINARY(32))
    signup_origin = db.Column(db.String(64), nullable=False)
    consent_version = db.Column(db.String(32), nullable=False)
    confirmed_at = db.Column(db.DateTime())
    unsubscribed_at = db.Column(db.DateTime())
    created_at = db.Column(db.DateTime(), nullable=False)
    updated_at = db.Column(db.DateTime(), nullable=False)
    __table_args__ = (
        db.UniqueConstraint('email_hash', name='uq_newsletter_email'),
        db.UniqueConstraint('confirm_token_hash', name='uq_newsletter_confirm_token'),
        db.UniqueConstraint('unsubscribe_token_hash', name='uq_newsletter_unsubscribe_token'),
        db.Index('ix_newsletter_active', 'status', 'id'),
    )


class NewsletterDelivery(db.Model):
    __tablename__ = 'newsletter_deliveries'
    id = db.Column(db.Integer(), primary_key=True, autoincrement=True, nullable=False)
    digest_id = db.Column(db.Integer(), nullable=False)
    subscriber_id = db.Column(db.Integer(), nullable=False)
    subscription_version = db.Column(db.Integer(), nullable=False)
    content_version = db.Column(db.Integer(), nullable=False)
    status = db.Column(db.String(16), nullable=False, server_default='queued')
    attempt_count = db.Column(db.Integer(), nullable=False, server_default='0')
    next_attempt_at = db.Column(db.DateTime(), nullable=False)
    lock_token = db.Column(db.String(64))
    lease_until = db.Column(db.DateTime())
    message_id = db.Column(db.String(191), nullable=False)
    provider_message_id = db.Column(db.String(191))
    last_error_code = db.Column(db.String(64))
    last_error_message = db.Column(db.String(1000))
    accepted_at = db.Column(db.DateTime())
    delivered_at = db.Column(db.DateTime())
    created_at = db.Column(db.DateTime(), nullable=False)
    updated_at = db.Column(db.DateTime(), nullable=False)
    __table_args__ = (
        db.UniqueConstraint('digest_id', 'subscriber_id', name='uq_newsletter_delivery'),
        db.UniqueConstraint('message_id', name='uq_newsletter_message'),
        db.Index('ix_newsletter_queue', 'status', 'next_attempt_at'),
        db.Index('ix_newsletter_delivery_subscriber', 'subscriber_id'),
        db.ForeignKeyConstraint(['digest_id'], ['ai_digests.id'], name='fk_newsletter_delivery_digest'),
        db.ForeignKeyConstraint(['subscriber_id'], ['newsletter_subscribers.id'], name='fk_newsletter_delivery_subscriber'),
    )
