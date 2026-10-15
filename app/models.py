"""
SQLAlchemy database models for ImageOpz.
SQLite (WAL) for quick local runs; Postgres schema is owned by Alembic (see alembic/).
"""

from sqlalchemy import MetaData, Column, String, Integer, BigInteger, Boolean, DateTime, Float, Text, Enum, Index, create_engine, false, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql.elements import conv
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import enum
import uuid
import logging

from app.config import settings

logger = logging.getLogger("imageopz.models")

# Deterministic constraint/index names so Alembic revisions are portable across backends.
# Explicitly named objects (e.g. idx_users_supabase_user_id_unique) keep their names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))


class JobStatus(str, enum.Enum):
    """Job processing status enum"""
    UPLOADING = "uploading"  # presigned, waiting for the browser's PUT and /uploads/complete
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"  # upload refused at /uploads/complete or expired; credit released


class CreditPool(str, enum.Enum):
    """Which counter a job's credit was reserved from (released from the same one)."""
    FREE = "free"  # users_quota.credits_used
    PRO = "pro"  # users_quota.pro_credits_used


class OrderProvider(str, enum.Enum):
    LEMONSQUEEZY = "lemonsqueezy"


class OrderStatus(str, enum.Enum):
    CREATED = "created"
    PAID = "paid"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

class CheckoutIntentStatus(str, enum.Enum):
    CREATED = "created"      # checkout created (not yet confirmed paid)
    PAID = "paid"            # payment confirmed (webhook or reconcile)
    EXPIRED = "expired"      # intent expired before payment confirmation
    REFUNDED = "refunded"    # paid order was refunded (no longer eligible for rehydration)


class User(Base):
    """
    Minimal user record for MVP.
    We currently identify paid users by email coming from the payment provider.
    """
    __tablename__ = "users"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(320), unique=True, index=True, nullable=False)
    # Indexed by the unique index idx_users_supabase_user_id_unique (see __table_args__).
    supabase_user_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Same name as the legacy raw-SQL index created in init_db (SQLite) so both paths agree.
        Index("idx_users_supabase_user_id_unique", "supabase_user_id", unique=True),
    )


class UsersQuota(Base):
    """
    Tracks quota usage for free tier users.
    Users are identified by IP hash (free tier) and/or linked to a user record (paid tier).
    """
    __tablename__ = "users_quota"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(64), ForeignKey("users.id"), nullable=True, index=True)
    ip_hash = Column(String(255), index=True, nullable=True)
    session_id = Column(String(255), index=True, nullable=True)
    credits_used = Column(Integer, default=0, server_default="0")
    is_pro = Column(Boolean, default=False, server_default=false())
    pro_expiry_date = Column(DateTime, nullable=True)
    pro_credits_used = Column(Integer, nullable=False, default=0, server_default="0")
    pro_verified = Column(Boolean, nullable=False, default=False, server_default=false())
    pro_source = Column(String(32), nullable=True)  # e.g. 'webhook' | 'restore' | 'unknown'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)




class Order(Base):
    """
    Stores payment-provider order/subscription events for support + idempotency.
    """
    __tablename__ = "orders"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider = Column(String(32), index=True, nullable=False)  # e.g. 'lemonsqueezy'
    external_id = Column(String(128), index=True, nullable=False)  # provider order id
    event_name = Column(String(64), nullable=True)

    user_id = Column(String(64), ForeignKey("users.id"), nullable=True, index=True)
    # The quota row whose Pro entitlement this order granted (lets a refund revoke exactly that grant).
    user_quota_id = Column(String(64), ForeignKey("users_quota.id", ondelete="SET NULL"), nullable=True, index=True)
    email = Column(String(320), index=True, nullable=True)

    status = Column(String(32), default=OrderStatus.UNKNOWN.value, index=True)
    raw_payload_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_orders_provider_external_id"),
        Index("idx_orders_provider_external_id", "provider", "external_id"),
    )

class ProcessedPaymentEvent(Base):
    """
    One row per applied payment event; the unique key makes redelivery a no-op (ADR-004).
    Inserted in the same transaction as the event's effects.
    """
    __tablename__ = "processed_payment_events"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider = Column(String(32), nullable=False)
    event_name = Column(String(64), nullable=False)
    object_type = Column(String(64), nullable=False)
    object_id = Column(String(128), nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "event_name", "object_type", "object_id"),
    )


class CheckoutIntent(Base):
    """
    Tracks a short-lived checkout "intent" so we can:
    - show activation pending state (frontend UX)
    - reconcile/rehydrate Pro to a new session_id if the browser/session changes
    - provide a server-side join key between checkout initiation and webhook activation
    """
    __tablename__ = "checkout_intents"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider = Column(String(32), index=True, nullable=False)  # 'lemonsqueezy'
    checkout_id = Column(String(128), index=True, nullable=True)  # provider checkout id (if known)

    email = Column(String(320), index=True, nullable=True)
    ip_hash = Column(String(255), index=True, nullable=True)
    session_id = Column(String(255), index=True, nullable=True)

    status = Column(String(32), default=CheckoutIntentStatus.CREATED.value, index=True)
    order_external_id = Column(String(128), index=True, nullable=True)  # provider order id

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime, index=True, nullable=True)
    paid_at = Column(DateTime, index=True, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "checkout_id", name="uq_checkout_intents_provider_checkout_id"),
        Index("idx_checkout_intents_session_status", "session_id", "status"),
        Index("idx_checkout_intents_ip_status", "ip_hash", "status"),
        Index("idx_checkout_intents_email_status", "email", "status"),
    )


class Job(Base):
    """
    Tracks image optimization jobs and their results.
    Each job represents a single image being processed.
    """
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(255), index=True)
    user_quota_id = Column(String(64), index=True)
    
    # VARCHAR + CHECK on every backend (conv() keeps the constraint name 'jobstatus', bypassing the ck_ convention); stores the member NAME (e.g. 'PENDING'), same as before.
    status = Column(
        Enum(JobStatus, native_enum=False, create_constraint=True, name=conv("jobstatus"), length=10),
        default=JobStatus.PENDING,
        index=True,
    )
    
    # File information
    original_filename = Column(String(255))
    original_size = Column(Integer)  # bytes
    optimized_size = Column(Integer, nullable=True)  # bytes

    # Original image dimensions (pixels)
    original_width = Column(Integer, nullable=True)
    original_height = Column(Integer, nullable=True)
    
    # Compression ratio
    compression_ratio = Column(Float, nullable=True)  # optimized_size / original_size
    
    # Storage
    r2_url = Column(String(512), nullable=True)
    r2_object_key = Column(String(255), nullable=True, unique=True)

    # Temp upload path (local disk, removed after processing)
    upload_path = Column(String(512), nullable=True)

    # Presigned upload (source object in the uploads bucket, deleted after a terminal outcome)
    source_object_key = Column(String(255), nullable=True, unique=True)
    declared_size_bytes = Column(BigInteger, nullable=True)
    declared_content_type = Column(String(50), nullable=True)
    upload_expires_at = Column(DateTime, nullable=True, index=True)
    rejection_reason = Column(String(32), nullable=True)
    credit_pool = Column(String(8), nullable=True)  # CreditPool value
    
    # Processing info
    quality_setting = Column(Integer)  # 50 for free, 75 for pro
    processing_error = Column(Text, nullable=True)

    # Processing timing (set by worker)
    processing_started_at = Column(DateTime, nullable=True)
    processing_finished_at = Column(DateTime, nullable=True)
    processing_duration_ms = Column(Integer, nullable=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expiry_date = Column(DateTime, index=True)
    deleted_at = Column(DateTime, nullable=True)
    


class Database:
    """Database connection and session management"""
    
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
    
    def init_db(self):
        """Initialize database connection; create tables only on the legacy SQLite path"""
        url = settings.sqlalchemy_database_url()
        is_sqlite = "sqlite" in url

        engine_kwargs = {"echo": settings.database_echo}
        if is_sqlite:
            # check_same_thread=False: allow cross-thread access (worker thread + main thread)
            # timeout=30: wait up to 30s if database is locked (handles API/Worker concurrency)
            engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            engine_kwargs.update(
                pool_pre_ping=True,
                pool_size=settings.db_pool_size,
                max_overflow=0,
            )
            if url.startswith("postgresql+psycopg:"):
                # psycopg3 auto-prepares statements after 5 executions; Supabase's transaction
                # pooler does not support prepared statements. None disables them.
                # sslmode comes from the URL query string.
                engine_kwargs["connect_args"] = {"prepare_threshold": None}

        self.engine = create_engine(url, **engine_kwargs)

        # Enable WAL mode for SQLite to prevent locked database errors
        if is_sqlite:
            with self.engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
                conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
                conn.exec_driver_sql("PRAGMA foreign_keys=ON;")
                conn.commit()

        # Create session factory
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

        if not is_sqlite:
            logger.info("Schema managed by Alembic; skipping DDL (run `alembic upgrade head`)")
            return

        # TODO(debt): remove once Postgres is the default for local dev
        # Create all tables
        Base.metadata.create_all(bind=self.engine)

        # Lightweight migrations for SQLite (add columns if missing)
        if "sqlite" in settings.database_url:
            try:
                with self.engine.connect() as conn:
                    # users: add supabase_user_id + unique index if missing
                    user_rows = conn.exec_driver_sql("PRAGMA table_info(users);").fetchall()
                    user_existing = {r[1] for r in user_rows}
                    if "supabase_user_id" not in user_existing:
                        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN supabase_user_id VARCHAR(128);")
                        logger.info("Migrated users: added supabase_user_id")
                    # Unique index. SQLite allows multiple NULLs even without partial index.
                    try:
                        conn.exec_driver_sql(
                            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_supabase_user_id_unique ON users(supabase_user_id);"
                        )
                    except Exception:
                        # Fallback: ignore if index cannot be created (should be rare).
                        logger.exception("Failed to create unique index for users.supabase_user_id")

                    # users_quota: add pro_verified, pro_source columns if they don't exist (SQLite supports ADD COLUMN)
                    rows = conn.exec_driver_sql("PRAGMA table_info(users_quota);").fetchall()
                    existing = {r[1] for r in rows}  # (cid, name, type, notnull, dflt_value, pk)
                    if "pro_credits_used" not in existing:
                        conn.exec_driver_sql("ALTER TABLE users_quota ADD COLUMN pro_credits_used INTEGER NOT NULL DEFAULT 0;")
                        logger.info("Migrated users_quota: added pro_credits_used")
                    if "pro_verified" not in existing:
                        conn.exec_driver_sql("ALTER TABLE users_quota ADD COLUMN pro_verified BOOLEAN NOT NULL DEFAULT 0;")
                        logger.info("Migrated users_quota: added pro_verified")
                    if "pro_source" not in existing:
                        conn.exec_driver_sql("ALTER TABLE users_quota ADD COLUMN pro_source VARCHAR(32);")
                        logger.info("Migrated users_quota: added pro_source")

                    # jobs: add original dimensions + processing timing columns if missing
                    job_rows = conn.exec_driver_sql("PRAGMA table_info(jobs);").fetchall()
                    job_existing = {r[1] for r in job_rows}
                    if "original_width" not in job_existing:
                        conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN original_width INTEGER;")
                        logger.info("Migrated jobs: added original_width")
                    if "original_height" not in job_existing:
                        conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN original_height INTEGER;")
                        logger.info("Migrated jobs: added original_height")
                    if "processing_started_at" not in job_existing:
                        conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN processing_started_at DATETIME;")
                        logger.info("Migrated jobs: added processing_started_at")
                    if "processing_finished_at" not in job_existing:
                        conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN processing_finished_at DATETIME;")
                        logger.info("Migrated jobs: added processing_finished_at")
                    if "processing_duration_ms" not in job_existing:
                        conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN processing_duration_ms INTEGER;")
                        logger.info("Migrated jobs: added processing_duration_ms")
                    conn.commit()
            except Exception:
                logger.exception("SQLite migration failed")
    
    def get_session(self):
        """Get a new database session"""
        if self.SessionLocal is None:
            self.init_db()
        return self.SessionLocal()


# Global database instance
db = Database()


def get_db():
    """Dependency for FastAPI to inject database session"""
    if db.SessionLocal is None:
        db.init_db()
    db_session = db.SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()
