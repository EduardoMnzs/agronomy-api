from datetime import datetime, timezone
from enum import Enum as PyEnum

import uuid
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Enum, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class UserRole(str, PyEnum):
    admin = "admin"
    user = "user"


class UserStatus(str, PyEnum):
    active = "active"
    inactive = "inactive"
    pending = "pending"


class DocumentCategory(str, PyEnum):
    solo = "solo"
    insumos = "insumos"
    sementes = "sementes"
    maquinas = "maquinas"
    herbicidas = "herbicidas"
    historico = "historico"
    outro = "outro"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column("full_name", String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.user, nullable=False)
    status = Column(Enum(UserStatus), default=UserStatus.active, nullable=False)
    last_active_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None))
    state = Column(String(2), nullable=True)
    city = Column(String(128), nullable=True)
    biome = Column(String(64), nullable=True)
    main_crop = Column(String(64), nullable=True)
    planting_system = Column(String(32), nullable=True)
    preferred_units = Column(String(16), nullable=True)
    profile_updated_at = Column(DateTime, nullable=True)
    avatar_path = Column(String(1024), nullable=True)

    session_documents = relationship("SessionDocument", back_populates="user")


class IndexStatus(str, PyEnum):
    queued = "queued"
    processing = "processing"
    done = "done"
    error = "error"


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    original_filename = Column(String(512), nullable=False)
    file_type = Column(String(20), nullable=False)
    file_path = Column(String(1024), nullable=False)
    index_path = Column(String(1024), nullable=True)
    category = Column(Enum(DocumentCategory), default=DocumentCategory.outro)
    description = Column(Text, nullable=True)
    indexed_at = Column(DateTime, nullable=True)
    indexed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status = Column(Enum(IndexStatus), default=IndexStatus.queued, nullable=False)
    status_message = Column(Text, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    messages = Column(JSON, nullable=False, default=list)
    pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None))
    updated_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None))


class SessionDocument(Base):
    __tablename__ = "session_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    original_filename = Column(String(512), nullable=False)
    file_type = Column(String(20), nullable=False)
    file_path = Column(String(1024), nullable=False)
    index_path = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None))
    expires_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="session_documents")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None), nullable=False)


class AccessRequestStatus(str, PyEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    organization = Column(String(255), nullable=True)
    message = Column(Text, nullable=True)
    status = Column(Enum(AccessRequestStatus), default=AccessRequestStatus.pending, nullable=False, index=True)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None), nullable=False, index=True)
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=True)
    is_secret = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None))


class UserDocument(Base):
    __tablename__ = "user_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    original_filename = Column(String(512), nullable=False)
    file_type = Column(String(20), nullable=False)
    file_path = Column(String(1024), nullable=False)
    index_path = Column(String(1024), nullable=True)
    category = Column(Enum(DocumentCategory), default=DocumentCategory.outro)
    description = Column(Text, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    status = Column(Enum(IndexStatus), default=IndexStatus.queued, nullable=False)
    status_message = Column(Text, nullable=True)
    indexed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None), nullable=False)
    expires_at = Column(DateTime, nullable=True, index=True)


class QueryLog(Base):
    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    question = Column(Text, nullable=False)
    model_used = Column(String(128), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(tz=timezone.utc).replace(tzinfo=None), nullable=False, index=True)

    # Feedback do usuário sobre a resposta: 1 = 👍, -1 = 👎, NULL = sem feedback.
    rating = Column(Integer, nullable=True, index=True)
    feedback_text = Column(Text, nullable=True)
    feedback_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_query_logs_created_success", "created_at", "success"),
    )
