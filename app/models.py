"""
SQLAlchemy database models for ImageOpz.
Uses SQLite with WAL mode to prevent database locked errors.
"""

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Float, Text, Enum, Index, create_engine, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import enum
import uuid
import logging

from app.config import settings

logger = logging.getLogger("imageopz.models")

Base = declarative_base()


class JobStatus(str, enum.Enum):
    """Job processing status enum"""
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


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


class User(Base):
    """
    Minimal user record for MVP.
    We currently identify paid users by email coming from the payment provider.
    """
    __tablename__ = "users"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(320), unique=True, index=True, nullable=False)
    supabase_user_id = Column(String(128), unique=False, index=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_user_email", "email"),
        Index("idx_users_supabase_user_id", "supabase_user_id"),
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
    credits_used = Column(Integer, default=0)
    is_pro = Column(Boolean, default=False)
    pro_expiry_date = Column(DateTime, nullable=True)
    pro_credits_used = Column(Integer, default=0)
    pro_verified = Column(Boolean, default=False)
    pro_source = Column(String(32), nullable=True)  # e.g. 'webhook' | 'restore' | 'unknown'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_ip_hash", "ip_hash"),
        Index("idx_session_id", "session_id"),
        Index("idx_user_id", "user_id"),
    )


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
    email = Column(String(320), index=True, nullable=True)

    status = Column(String(32), default=OrderStatus.UNKNOWN.value, index=True)
    raw_payload_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_orders_provider_external_id"),
        Index("idx_orders_provider_external_id", "provider", "external_id"),
        Index("idx_orders_user_id", "user_id"),
        Index("idx_orders_email", "email"),
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
    
    status = Column(Enum(JobStatus), default=JobStatus.PENDING, index=True)
    
    # File information
    original_filename = Column(String(255))
    original_size = Column(Integer)  # bytes
    optimized_size = Column(Integer, nullable=True)  # bytes
    
    # Compression ratio
    compression_ratio = Column(Float, nullable=True)  # optimized_size / original_size
    
    # Storage
    r2_url = Column(String(512), nullable=True)
    r2_object_key = Column(String(255), nullable=True, unique=True)

    # Temp upload path (local disk, removed after processing)
    upload_path = Column(String(512), nullable=True)
    
    # Processing info
    quality_setting = Column(Integer)  # 50 for free, 75 for pro
    processing_error = Column(Text, nullable=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expiry_date = Column(DateTime, index=True)
    deleted_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        Index("idx_status", "status"),
        Index("idx_expiry_date", "expiry_date"),
        Index("idx_user_quota_id", "user_quota_id"),
    )


class Database:
    """Database connection and session management"""
    
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
    
    def init_db(self):
        """Initialize database connection and create tables"""
        # Create engine with SQLite and WAL mode
        connect_args = {}
        if "sqlite" in settings.database_url:
            # check_same_thread=False: allow cross-thread access (worker thread + main thread)
            # timeout=30: wait up to 30s if database is locked (handles API/Worker concurrency)
            connect_args = {"check_same_thread": False, "timeout": 30}
        
        self.engine = create_engine(
            settings.database_url,
            echo=settings.database_echo,
            connect_args=connect_args if connect_args else {}
        )
        
        # Enable WAL mode for SQLite to prevent locked database errors
        if "sqlite" in settings.database_url:
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
