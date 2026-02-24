"""SQLAlchemy ORM models for BrainstormAI."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class SessionModel(Base):
    """A brainstorming session."""

    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(256), nullable=True)
    topic = Column(Text, nullable=False)
    status = Column(
        SAEnum("active", "ended", name="session_status"),
        nullable=False,
        default="active",
    )
    agent_count = Column(Integer, nullable=False)
    model_config_snapshot = Column(Text, nullable=True)  # JSON string of per-agent model configs
    created_at = Column(DateTime, nullable=False, default=func.now())
    ended_at = Column(DateTime, nullable=True)

    agents = relationship("AgentModel", back_populates="session", lazy="selectin")
    messages = relationship("MessageModel", back_populates="session", lazy="selectin", order_by="MessageModel.created_at")


class AgentModel(Base):
    """An AI agent persona — immutable after creation."""

    __tablename__ = "agents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    nickname = Column(String(64), nullable=False)
    persona = Column(Text, nullable=False)  # Personality description
    style = Column(Text, nullable=False)  # Speaking style description
    model_name = Column(String(128), nullable=True)  # Per-agent model override
    created_at = Column(DateTime, nullable=False, default=func.now())

    session = relationship("SessionModel", back_populates="agents")


class MessageModel(Base):
    """A single message in the chat."""

    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    author_type = Column(
        SAEnum("user", "ai", "system", name="author_type"),
        nullable=False,
    )
    author_id = Column(String(36), nullable=True)  # agent_id for AI, null for user/system
    author_name = Column(String(64), nullable=True)  # Display name
    target_message_id = Column(String(36), ForeignKey("messages.id"), nullable=True)
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=func.now())

    session = relationship("SessionModel", back_populates="messages")


class EventModel(Base):
    """Streaming events and system events for replay / export."""

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    message_id = Column(String(36), ForeignKey("messages.id"), nullable=True)
    event_type = Column(String(64), nullable=False)  # message_started, message_delta, message_completed, status, error
    payload = Column(Text, nullable=False)  # JSON payload
    created_at = Column(DateTime, nullable=False, default=func.now())
