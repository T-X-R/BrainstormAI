"""Pydantic schemas for API request/response and internal domain objects."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------- Enums ----------

class SessionStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"


class AuthorType(str, Enum):
    USER = "user"
    AI = "ai"
    SYSTEM = "system"


class AgentAction(str, Enum):
    """What an agent decides to do after seeing the latest messages."""
    SILENT = "silent"
    REPLY_USER = "reply_user"
    REPLY_AI = "reply_ai"
    COMMENT = "comment"


# ---------- Agent Config (request) ----------

class AgentConfig(BaseModel):
    """Per-agent config supplied when creating a session."""
    model_name: str | None = None  # Override the default model


# ---------- Session Create ----------

class SessionCreateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=1024, description="The brainstorming question / topic")
    agent_count: int = Field(default=3, ge=1, le=5, description="Number of AI agents (1-5)")
    agent_configs: list[AgentConfig] | None = None  # Optional per-agent model overrides
    title: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str
    topic: str
    agents: list[AgentInfo]


class AgentInfo(BaseModel):
    id: str
    nickname: str
    persona: str
    style: str
    model_name: str | None = None


class AvailableModelsResponse(BaseModel):
    models: list[str]
    default_model: str


# ---------- Persona Generation ----------

class PersonaProfile(BaseModel):
    """Structured persona payload generated at session creation."""
    nickname: str
    persona: str
    style: str


class PersonaBatch(BaseModel):
    """Structured batch payload for persona generation."""
    personas: list[PersonaProfile] = Field(default_factory=list)


# Rebuild to resolve forward ref
SessionCreateResponse.model_rebuild()


# ---------- Message ----------

class MessageOut(BaseModel):
    id: str
    session_id: str
    author_type: AuthorType
    author_id: str | None = None
    author_name: str | None = None
    target_message_id: str | None = None
    content: str
    created_at: datetime


# ---------- Agent Decision (internal, structured output from LLM) ----------

class AgentDecision(BaseModel):
    """Structured decision output from an agent's decision prompt."""
    action: AgentAction
    reason: str | None = None  # Why the agent chose this action
    target_message_id: str | None = None
    target_author_name: str | None = None
    stance: str | None = None  # e.g. "agree", "disagree", "neutral", "curious"
    key_points: str | None = None  # Brief outline of what to say
    confidence: float | None = Field(default=None, ge=0, le=1)


# ---------- WebSocket Protocol ----------

class WSClientEvent(BaseModel):
    """Event sent from client to server over WebSocket."""
    type: str  # "user_message" | "stop" | "end_session"
    content: str | None = None  # For user_message


class WSServerEvent(BaseModel):
    """Event sent from server to client over WebSocket."""
    type: str  # "message_started" | "message_delta" | "message_completed" | "message_cancelled" | "status" | "error" | "session_ended" | "agents_ready"
    data: dict[str, Any] = Field(default_factory=dict)


# ---------- Export ----------

class SessionExport(BaseModel):
    session_id: str
    topic: str
    title: str | None = None
    status: str
    created_at: datetime
    ended_at: datetime | None = None
    agents: list[AgentInfo]
    messages: list[MessageOut]
