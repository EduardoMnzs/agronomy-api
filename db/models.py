from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Enum, Text
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class UserRole(str, PyEnum):
    admin = "admin"
    user = "user"


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
    name = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.user, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session_documents = relationship("SessionDocument", back_populates="user")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    original_filename = Column(String(512), nullable=False)
    file_type = Column(String(20), nullable=False)  # pdf, docx, csv, xlsx, json
    file_path = Column(String(1024), nullable=False)
    index_path = Column(String(1024), nullable=True)
    category = Column(Enum(DocumentCategory), default=DocumentCategory.outro)
    description = Column(Text, nullable=True)
    indexed_at = Column(DateTime, nullable=True)
    indexed_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class SessionDocument(Base):
    __tablename__ = "session_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    original_filename = Column(String(512), nullable=False)
    file_type = Column(String(20), nullable=False)
    file_path = Column(String(1024), nullable=False)
    index_path = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="session_documents")
