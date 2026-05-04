"""
SQLAlchemy database models for ImageOpz.
Uses SQLite with WAL mode to prevent database locked errors.
"""

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Float, Text, Enum, Index, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import enum
import uuid

from app.config import settings

Base = declarative_base()


class JobStatus(str, enum.Enum):
    """Job processing status enum"""
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class UsersQuota(Base):
    """
    Tracks quota usage for free tier users.
    Users are identified by IP hash or session ID.
    """
    __tablename__ = "users_quota"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    ip_hash = Column(String(255), unique=True, index=True)
    session_id = Column(String(255), unique=True, index=True, nullable=True)
    credits_used = Column(Integer, default=0)
    is_pro = Column(Boolean, default=False)
    pro_expiry_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_ip_hash", "ip_hash"),
        Index("idx_session_id", "session_id"),
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
        self.engine = create_engine(
            settings.database_url,
            echo=settings.database_echo,
            connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {}
        )
        
        # Enable WAL mode for SQLite to prevent locked database errors
        if "sqlite" in settings.database_url:
            with self.engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
                conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
                conn.commit()
        
        # Create session factory
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )
        
        # Create all tables
        Base.metadata.create_all(bind=self.engine)
    
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
