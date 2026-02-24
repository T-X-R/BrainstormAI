"""Repository layer for database CRUD operations."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import AgentModel, EventModel, MessageModel, SessionModel


class SessionRepository:
    """CRUD for sessions."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        topic: str,
        agent_count: int,
        title: str | None = None,
        model_config_snapshot: dict[str, Any] | None = None,
    ) -> SessionModel:
        session = SessionModel(
            id=str(uuid.uuid4()),
            title=title,
            topic=topic,
            status="active",
            agent_count=agent_count,
            model_config_snapshot=json.dumps(model_config_snapshot) if model_config_snapshot else None,
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def get(self, session_id: str) -> SessionModel | None:
        result = await self.db.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )
        return result.scalar_one_or_none()

    async def end_session(self, session_id: str) -> None:
        await self.db.execute(
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(status="ended", ended_at=datetime.utcnow())
        )
        await self.db.commit()


class AgentRepository:
    """CRUD for agents (write-once, read-many)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        session_id: str,
        nickname: str,
        persona: str,
        style: str,
        model_name: str | None = None,
    ) -> AgentModel:
        agent = AgentModel(
            id=str(uuid.uuid4()),
            session_id=session_id,
            nickname=nickname,
            persona=persona,
            style=style,
            model_name=model_name,
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def list_by_session(self, session_id: str) -> list[AgentModel]:
        result = await self.db.execute(
            select(AgentModel).where(AgentModel.session_id == session_id)
        )
        return list(result.scalars().all())


class MessageRepository:
    """CRUD for messages."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        session_id: str,
        author_type: str,
        content: str,
        author_id: str | None = None,
        author_name: str | None = None,
        target_message_id: str | None = None,
    ) -> MessageModel:
        msg = MessageModel(
            id=str(uuid.uuid4()),
            session_id=session_id,
            author_type=author_type,
            author_id=author_id,
            author_name=author_name,
            target_message_id=target_message_id,
            content=content,
        )
        self.db.add(msg)
        await self.db.commit()
        await self.db.refresh(msg)
        return msg

    async def update_content(self, message_id: str, content: str) -> None:
        await self.db.execute(
            update(MessageModel)
            .where(MessageModel.id == message_id)
            .values(content=content)
        )
        await self.db.commit()

    async def list_by_session(
        self, session_id: str, limit: int | None = None
    ) -> list[MessageModel]:
        stmt = (
            select(MessageModel)
            .where(MessageModel.session_id == session_id)
            .order_by(MessageModel.created_at)
        )
        if limit:
            stmt = stmt.order_by(MessageModel.created_at.desc()).limit(limit)
            # Re-order ascending after limiting
            result = await self.db.execute(stmt)
            messages = list(result.scalars().all())
            messages.reverse()
            return messages
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, message_id: str) -> MessageModel | None:
        result = await self.db.execute(
            select(MessageModel).where(MessageModel.id == message_id)
        )
        return result.scalar_one_or_none()


class EventRepository:
    """CRUD for streaming / system events."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        message_id: str | None = None,
    ) -> EventModel:
        event = EventModel(
            session_id=session_id,
            message_id=message_id,
            event_type=event_type,
            payload=json.dumps(payload, ensure_ascii=False),
        )
        self.db.add(event)
        await self.db.commit()
        return event

    async def list_by_session(self, session_id: str) -> list[EventModel]:
        result = await self.db.execute(
            select(EventModel)
            .where(EventModel.session_id == session_id)
            .order_by(EventModel.created_at)
        )
        return list(result.scalars().all())
