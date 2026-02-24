"""HTTP API endpoints for session management and export."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from src.domain.schemas import (
    AgentInfo,
    AvailableModelsResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionExport,
    MessageOut,
)
from src.config.settings import get_settings
from src.infra.db.engine import get_session_factory
from src.infra.db.repository import (
    AgentRepository,
    EventRepository,
    MessageRepository,
    SessionRepository,
)
from src.services.session import (
    create_session,
    create_orchestrator,
    end_session as end_session_service,
)

router = APIRouter()


@router.get("/models", response_model=AvailableModelsResponse)
async def list_models_endpoint() -> AvailableModelsResponse:
    """Return selectable model names for frontend."""
    llm = get_settings().llm
    models = llm.available_models or [llm.default_model]
    return AvailableModelsResponse(
        models=models,
        default_model=llm.default_model,
    )


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session_endpoint(req: SessionCreateRequest) -> SessionCreateResponse:
    """Create a new brainstorming session with AI personas."""
    try:
        session_id, agents = await create_session(
            topic=req.topic,
            agent_count=req.agent_count,
            agent_configs=req.agent_configs,
            title=req.title,
        )

        # Create the orchestrator so it's ready when WebSocket connects
        create_orchestrator(session_id=session_id, topic=req.topic, agents=agents)

        return SessionCreateResponse(
            session_id=session_id,
            topic=req.topic,
            agents=agents,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to create session: {}", e)
        raise HTTPException(status_code=500, detail=f"Failed to create session: {e}")


@router.post("/sessions/{session_id}/end")
async def end_session_endpoint(session_id: str) -> dict[str, str]:
    """End a brainstorming session."""
    factory = get_session_factory()
    async with factory() as db:
        session_repo = SessionRepository(db)
        session = await session_repo.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

    await end_session_service(session_id)
    return {"status": "ended", "session_id": session_id}


@router.get("/sessions/{session_id}/export")
async def export_session_endpoint(session_id: str) -> JSONResponse:
    """Export a session's complete data as JSON."""
    factory = get_session_factory()
    async with factory() as db:
        session_repo = SessionRepository(db)
        session = await session_repo.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        agent_repo = AgentRepository(db)
        agents = await agent_repo.list_by_session(session_id)

        msg_repo = MessageRepository(db)
        messages = await msg_repo.list_by_session(session_id)

    # Build export
    export = SessionExport(
        session_id=session.id,
        topic=session.topic,
        title=session.title,
        status=session.status,
        created_at=session.created_at,
        ended_at=session.ended_at,
        agents=[
            AgentInfo(
                id=a.id,
                nickname=a.nickname,
                persona=a.persona,
                style=a.style,
                model_name=a.model_name,
            )
            for a in agents
        ],
        messages=[
            MessageOut(
                id=m.id,
                session_id=m.session_id,
                author_type=m.author_type,
                author_id=m.author_id,
                author_name=m.author_name,
                target_message_id=m.target_message_id,
                content=m.content,
                created_at=m.created_at,
            )
            for m in messages
        ],
    )

    return JSONResponse(
        content=json.loads(export.model_dump_json()),
        headers={
            "Content-Disposition": f'attachment; filename="brainstorm_{session_id}.json"',
        },
    )
