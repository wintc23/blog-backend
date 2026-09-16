"""Add AI digest, source provenance and newsletter tables.

Revision ID: 20260916_ai_digest
Revises: 20260915_profile_contacts
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "20260916_ai_digest"
down_revision = "20260915_profile_contacts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_digest_settings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=False, nullable=False),
        sa.Column('title', sa.String(128), nullable=False),
        sa.Column('timezone', sa.String(64), nullable=False, server_default='Asia/Shanghai'),
        sa.Column('publish_time', sa.Time(), nullable=False, server_default='09:00:00'),
        sa.Column('auto_publish', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('email_enabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('lookback_hours', sa.Integer(), nullable=False, server_default='24'),
        sa.Column('max_lookback_hours', sa.Integer(), nullable=False, server_default='72'),
        sa.Column('target_min_chars', sa.Integer(), nullable=False, server_default='1000'),
        sa.Column('target_max_chars', sa.Integer(), nullable=False, server_default='1500'),
        sa.Column('preferences_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column('generator_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column('prompt_template', sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )

    op.create_table('ai_news_sources',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('endpoint_url', sa.Text(), nullable=False),
        sa.Column('endpoint_hash', sa.BINARY(32), nullable=False),
        sa.Column('homepage_url', sa.Text()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('config_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column('etag', sa.String(512)),
        sa.Column('last_modified', sa.String(128)),
        sa.Column('last_success_at', sa.DateTime()),
        sa.Column('last_error', sa.String(1000)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('endpoint_hash', name='uq_ai_source_endpoint'),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )

    op.create_table('ai_news_items',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(512)),
        sa.Column('canonical_url', sa.Text(), nullable=False),
        sa.Column('url_hash', sa.BINARY(32), nullable=False),
        sa.Column('title', sa.String(512), nullable=False),
        sa.Column('published_date', sa.Date()),
        sa.Column('published_at', sa.DateTime()),
        sa.Column('date_precision', sa.String(16), nullable=False, server_default='unknown'),
        sa.Column('source_updated_at', sa.DateTime()),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('language', sa.String(16)),
        sa.Column('event_key', sa.String(191)),
        sa.Column('evidence_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column('content_hash', sa.BINARY(32), nullable=False),
        sa.Column('selection_status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('skip_reason', sa.String(500)),
        sa.UniqueConstraint('url_hash', name='uq_ai_item_url'),
        sa.ForeignKeyConstraint(['source_id'], ['ai_news_sources.id'], name='fk_ai_item_source'),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index('ix_ai_item_source_date', 'ai_news_items', ['source_id', 'published_date'])
    op.create_index('ix_ai_item_selection_seen', 'ai_news_items', ['selection_status', 'first_seen_at'])
    op.create_index('ix_ai_item_event', 'ai_news_items', ['event_key'])

    op.create_table('ai_digests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('issue_date', sa.Date(), nullable=False),
        sa.Column('timezone', sa.String(64), nullable=False),
        sa.Column('slug', sa.String(191), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('content_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column('content_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(16), nullable=False, server_default='draft'),
        sa.Column('source_window_start', sa.DateTime(), nullable=False),
        sa.Column('source_window_end', sa.DateTime(), nullable=False),
        sa.Column('mail_snapshot_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql")),
        sa.Column('mail_content_version', sa.Integer()),
        sa.Column('scheduled_publish_at', sa.DateTime(), nullable=False),
        sa.Column('published_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('issue_date', name='uq_ai_digest_date'),
        sa.UniqueConstraint('slug', name='uq_ai_digest_slug'),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index('ix_ai_digest_public', 'ai_digests', ['status', 'issue_date'])

    op.create_table('ai_digest_items',
        sa.Column('digest_id', sa.Integer(), nullable=False),
        sa.Column('content_version', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('block_id', sa.String(64), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('evidence_snapshot_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.PrimaryKeyConstraint('digest_id', 'content_version', 'block_id', 'item_id'),
        sa.ForeignKeyConstraint(['digest_id'], ['ai_digests.id'], name='fk_ai_di_digest'),
        sa.ForeignKeyConstraint(['item_id'], ['ai_news_items.id'], name='fk_ai_di_item'),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index('ix_ai_digest_item', 'ai_digest_items', ['item_id'])

    op.create_table('ai_digest_runs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('run_key', sa.String(64), nullable=False),
        sa.Column('digest_id', sa.Integer()),
        sa.Column('issue_date', sa.Date(), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('trigger_kind', sa.String(16), nullable=False),
        sa.Column('stage', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('lock_token', sa.String(64)),
        sa.Column('lease_until', sa.DateTime()),
        sa.Column('settings_snapshot_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column('result_json', sa.Text().with_variant(MEDIUMTEXT(), "mysql")),
        sa.Column('error_code', sa.String(64)),
        sa.Column('error_message', sa.String(1000)),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime()),
        sa.UniqueConstraint('run_key', name='uq_ai_run_key'),
        sa.ForeignKeyConstraint(['digest_id'], ['ai_digests.id'], name='fk_ai_run_digest'),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index('ix_ai_run_date', 'ai_digest_runs', ['issue_date', 'started_at'])
    op.create_index('ix_ai_run_recovery', 'ai_digest_runs', ['status', 'lease_until'])

    op.create_table('newsletter_subscribers',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('email', sa.String(254), nullable=False),
        sa.Column('email_normalized', sa.String(254), nullable=False),
        sa.Column('email_hash', sa.BINARY(32), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('subscription_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('confirm_token_hash', sa.BINARY(32)),
        sa.Column('confirm_expires_at', sa.DateTime()),
        sa.Column('confirmation_sent_at', sa.DateTime()),
        sa.Column('unsubscribe_token_hash', sa.BINARY(32)),
        sa.Column('signup_origin', sa.String(64), nullable=False),
        sa.Column('consent_version', sa.String(32), nullable=False),
        sa.Column('confirmed_at', sa.DateTime()),
        sa.Column('unsubscribed_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('email_hash', name='uq_newsletter_email'),
        sa.UniqueConstraint('confirm_token_hash', name='uq_newsletter_confirm_token'),
        sa.UniqueConstraint('unsubscribe_token_hash', name='uq_newsletter_unsubscribe_token'),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index('ix_newsletter_active', 'newsletter_subscribers', ['status', 'id'])

    op.create_table('newsletter_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('digest_id', sa.Integer(), nullable=False),
        sa.Column('subscriber_id', sa.Integer(), nullable=False),
        sa.Column('subscription_version', sa.Integer(), nullable=False),
        sa.Column('content_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='queued'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
        sa.Column('lock_token', sa.String(64)),
        sa.Column('lease_until', sa.DateTime()),
        sa.Column('message_id', sa.String(191), nullable=False),
        sa.Column('provider_message_id', sa.String(191)),
        sa.Column('last_error_code', sa.String(64)),
        sa.Column('last_error_message', sa.String(1000)),
        sa.Column('accepted_at', sa.DateTime()),
        sa.Column('delivered_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('digest_id', 'subscriber_id', name='uq_newsletter_delivery'),
        sa.UniqueConstraint('message_id', name='uq_newsletter_message'),
        sa.ForeignKeyConstraint(['digest_id'], ['ai_digests.id'], name='fk_newsletter_delivery_digest'),
        sa.ForeignKeyConstraint(['subscriber_id'], ['newsletter_subscribers.id'], name='fk_newsletter_delivery_subscriber'),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index('ix_newsletter_queue', 'newsletter_deliveries', ['status', 'next_attempt_at'])
    op.create_index('ix_newsletter_delivery_subscriber', 'newsletter_deliveries', ['subscriber_id'])


def downgrade():
    op.drop_table('newsletter_deliveries')
    op.drop_table('newsletter_subscribers')
    op.drop_table('ai_digest_runs')
    op.drop_table('ai_digest_items')
    op.drop_table('ai_digests')
    op.drop_table('ai_news_items')
    op.drop_table('ai_news_sources')
    op.drop_table('ai_digest_settings')
