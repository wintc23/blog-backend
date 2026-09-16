"""Durable generation queue and versioned content (additive only)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = '20260916_generation'
down_revision = '20260916_ai_digest'
branch_labels = None
depends_on = None


def col(name, kind, nullable=False, **kw):
    return sa.Column(name, kind, nullable=nullable, **kw)


def pk():
    return col('id', sa.Integer(), primary_key=True)


def doc(name):
    return col(name, sa.Text().with_variant(MEDIUMTEXT(), 'mysql'))


def fk(name, target, nullable=False):
    return sa.Column(name, sa.Integer(), sa.ForeignKey(target), nullable=nullable)


def table(name, *columns):
    op.create_table(name, *columns, mysql_engine='InnoDB', mysql_charset='utf8mb4')


def upgrade():
    table('generation_tasks', pk(), col('name', sa.String(128)), col('content_type', sa.String(64)),
          col('channel', sa.String(64)), col('environment', sa.String(32)), col('enabled', sa.Boolean()),
          col('version', sa.Integer()), col('created_at', sa.DateTime()), col('updated_at', sa.DateTime()))
    table('generation_task_versions', pk(), fk('task_id', 'generation_tasks.id'), col('version', sa.Integer()),
          doc('config_json'), col('created_at', sa.DateTime()),
          sa.UniqueConstraint('task_id', 'version', name='uq_generation_task_version'))
    table('generation_jobs', pk(), col('job_key', sa.String(64), unique=True),
          fk('task_id', 'generation_tasks.id'), fk('version_id', 'generation_task_versions.id'),
          col('environment', sa.String(32)), col('edition', sa.Date()), col('purpose', sa.String(16)),
          col('status', sa.String(16)), col('stage', sa.String(32)), col('attempt', sa.Integer()),
          col('next_attempt_at', sa.DateTime()), col('deadline_at', sa.DateTime()),
          col('lock_token', sa.String(64), True), col('lease_until', sa.DateTime(), True), doc('checkpoint_json'),
          col('error_code', sa.String(64), True), col('error_message', sa.String(1000), True),
          col('created_at', sa.DateTime()), col('updated_at', sa.DateTime()))
    op.create_index('ix_generation_queue', 'generation_jobs', ['environment', 'status', 'next_attempt_at'])
    table('generation_runs', pk(), fk('job_id', 'generation_jobs.id'), col('attempt', sa.Integer()),
          col('status', sa.String(16)), col('stage', sa.String(32)), doc('result_json'),
          col('error_code', sa.String(64), True), col('error_message', sa.String(1000), True),
          col('started_at', sa.DateTime()), col('finished_at', sa.DateTime(), True),
          sa.UniqueConstraint('job_id', 'attempt', name='uq_generation_attempt'))
    table('generated_contents', pk(), col('channel', sa.String(64)), col('content_type', sa.String(64)),
          col('edition', sa.Date()), fk('task_id', 'generation_tasks.id', True), col('environment', sa.String(32)),
          col('status', sa.String(16)), col('current_revision', sa.Integer()), col('published_revision', sa.Integer(), True),
          sa.Column('legacy_digest_id', sa.Integer(), sa.ForeignKey('ai_digests.id'), unique=True),
          col('scheduled_publish_at', sa.DateTime()), col('deadline_at', sa.DateTime()), col('auto_publish', sa.Boolean()),
          col('created_at', sa.DateTime()), col('updated_at', sa.DateTime()),
          sa.UniqueConstraint('channel', 'content_type', 'edition', name='uq_generated_edition'))
    op.create_index('ix_generated_publication', 'generated_contents', ['environment', 'status', 'scheduled_publish_at'])
    table('content_revisions', pk(), fk('content_id', 'generated_contents.id'), col('revision', sa.Integer()),
          fk('run_id', 'generation_runs.id', True), col('origin', sa.String(16)), doc('document_json'),
          col('validated', sa.Boolean()), col('created_at', sa.DateTime()),
          sa.UniqueConstraint('content_id', 'revision', name='uq_content_revision'))
    table('generation_heartbeats', col('identity', sa.String(128), primary_key=True), col('environment', sa.String(32)),
          col('role', sa.String(16)), col('last_seen_at', sa.DateTime()))


def downgrade():
    for name in ('generation_heartbeats', 'content_revisions', 'generated_contents', 'generation_runs',
                 'generation_jobs', 'generation_task_versions', 'generation_tasks'):
        op.drop_table(name)
