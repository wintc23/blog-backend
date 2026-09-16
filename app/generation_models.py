"""Generic, durable generation jobs and immutable content revisions."""
from datetime import datetime
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from . import db


def document():
    return db.Column(db.Text().with_variant(MEDIUMTEXT(), 'mysql'), nullable=False)


class GenerationTask(db.Model):
    __tablename__ = 'generation_tasks'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    content_type = db.Column(db.String(64), nullable=False)
    channel = db.Column(db.String(64), nullable=False)
    environment = db.Column(db.String(32), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class GenerationTaskVersion(db.Model):
    __tablename__ = 'generation_task_versions'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('generation_tasks.id'), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    config_json = document()
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('task_id', 'version', name='uq_generation_task_version'),)


class GenerationJob(db.Model):
    __tablename__ = 'generation_jobs'
    id = db.Column(db.Integer, primary_key=True)
    job_key = db.Column(db.String(64), unique=True, nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('generation_tasks.id'), nullable=False)
    version_id = db.Column(db.Integer, db.ForeignKey('generation_task_versions.id'), nullable=False)
    environment = db.Column(db.String(32), nullable=False)
    edition = db.Column(db.Date, nullable=False)
    purpose = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(16), nullable=False)
    stage = db.Column(db.String(32), nullable=False, default='collect')
    attempt = db.Column(db.Integer, nullable=False, default=0)
    next_attempt_at = db.Column(db.DateTime, nullable=False)
    deadline_at = db.Column(db.DateTime, nullable=False)
    lock_token = db.Column(db.String(64))
    lease_until = db.Column(db.DateTime)
    checkpoint_json = document()
    error_code = db.Column(db.String(64))
    error_message = db.Column(db.String(1000))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.Index('ix_generation_queue', 'environment', 'status', 'next_attempt_at'),)


class GenerationRun(db.Model):
    __tablename__ = 'generation_runs'
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey('generation_jobs.id'), nullable=False)
    attempt = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False)
    stage = db.Column(db.String(32), nullable=False)
    result_json = document()
    error_code = db.Column(db.String(64))
    error_message = db.Column(db.String(1000))
    started_at = db.Column(db.DateTime, nullable=False)
    finished_at = db.Column(db.DateTime)
    __table_args__ = (db.UniqueConstraint('job_id', 'attempt', name='uq_generation_attempt'),)


class GeneratedContent(db.Model):
    __tablename__ = 'generated_contents'
    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(64), nullable=False)
    content_type = db.Column(db.String(64), nullable=False)
    edition = db.Column(db.Date, nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('generation_tasks.id'))
    environment = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), nullable=False)
    current_revision = db.Column(db.Integer, nullable=False)
    published_revision = db.Column(db.Integer)
    legacy_digest_id = db.Column(db.Integer, db.ForeignKey('ai_digests.id'), unique=True)
    scheduled_publish_at = db.Column(db.DateTime, nullable=False)
    deadline_at = db.Column(db.DateTime, nullable=False)
    auto_publish = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.UniqueConstraint('channel', 'content_type', 'edition', name='uq_generated_edition'),
        db.Index('ix_generated_publication', 'environment', 'status', 'scheduled_publish_at'),
    )


class ContentRevision(db.Model):
    __tablename__ = 'content_revisions'
    id = db.Column(db.Integer, primary_key=True)
    content_id = db.Column(db.Integer, db.ForeignKey('generated_contents.id'), nullable=False)
    revision = db.Column(db.Integer, nullable=False)
    run_id = db.Column(db.Integer, db.ForeignKey('generation_runs.id'))
    origin = db.Column(db.String(16), nullable=False)
    document_json = document()
    validated = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('content_id', 'revision', name='uq_content_revision'),)


class GenerationHeartbeat(db.Model):
    __tablename__ = 'generation_heartbeats'
    identity = db.Column(db.String(128), primary_key=True)
    environment = db.Column(db.String(32), nullable=False)
    role = db.Column(db.String(16), nullable=False)
    last_seen_at = db.Column(db.DateTime, nullable=False)
