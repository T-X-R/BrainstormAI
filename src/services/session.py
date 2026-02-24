"""Session lifecycle service — create sessions, generate personas, manage orchestrators."""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.config.settings import get_settings
from src.domain.schemas import AgentConfig, AgentInfo
from src.infra.db.engine import get_session_factory
from src.infra.db.repository import (
    AgentRepository,
    EventRepository,
    MessageRepository,
    SessionRepository,
)
from src.services.orchestrator import SessionOrchestrator
from src.services.persona import generate_personas

# Global registry of active orchestrators (session_id -> orchestrator)
_active_orchestrators: dict[str, SessionOrchestrator] = {}


async def create_session(
    topic: str,
    agent_count: int,
    agent_configs: list[AgentConfig] | None = None,
    title: str | None = None,
) -> tuple[str, list[AgentInfo]]:
    """Create a new brainstorming session with AI personas.

    Returns:
        Tuple of (session_id, list of AgentInfo).
    """
    settings = get_settings()

    # Validate
    if agent_count < 1 or agent_count > settings.session.max_agents:
        raise ValueError(f"agent_count must be between 1 and {settings.session.max_agents}")

    # Normalize agent_configs
    configs = agent_configs or [AgentConfig() for _ in range(agent_count)]
    if len(configs) < agent_count:
        configs.extend([AgentConfig() for _ in range(agent_count - len(configs))])
    allowed_models = set(settings.llm.available_models)
    if allowed_models:
        invalid_models = sorted(
            {
                cfg.model_name
                for cfg in configs
                if cfg.model_name and cfg.model_name not in allowed_models
            }
        )
        if invalid_models:
            raise ValueError(
                "Unsupported model_name values: "
                f"{', '.join(invalid_models)}. Allowed: {', '.join(sorted(allowed_models))}"
            )

    factory = get_session_factory()
    async with factory() as db:
        # Create session record
        session_repo = SessionRepository(db)
        config_snapshot = [c.model_dump() for c in configs]
        session = await session_repo.create(
            topic=topic,
            agent_count=agent_count,
            title=title,
            model_config_snapshot=config_snapshot,
        )
        session_id = session.id

        # Generate personas in a single shot for better global diversity.
        agent_repo = AgentRepository(db)
        # Persona generation uses the configured default model.
        persona_model_name = settings.llm.default_model
        persona_results = await generate_personas(
            topic=topic,
            agent_count=agent_count,
            model_name=persona_model_name,
        )

        agents_info: list[AgentInfo] = []
        for i, persona_data in enumerate(persona_results):
            agent = await agent_repo.create(
                session_id=session_id,
                nickname=persona_data["nickname"],
                persona=persona_data["persona"],
                style=persona_data["style"],
                model_name=configs[i].model_name,
            )
            agents_info.append(
                AgentInfo(
                    id=agent.id,
                    nickname=agent.nickname,
                    persona=agent.persona,
                    style=agent.style,
                    model_name=agent.model_name,
                )
            )

        logger.info(
            "Session {} created with {} agents: {}",
            session_id,
            agent_count,
            [a.nickname for a in agents_info],
        )

        return session_id, agents_info


def create_orchestrator(
    session_id: str,
    topic: str,
    agents: list[AgentInfo],
) -> SessionOrchestrator:
    """Create and register a session orchestrator."""
    orch = SessionOrchestrator(session_id=session_id, topic=topic)
    for agent in agents:
        orch.add_agent(agent)
    _active_orchestrators[session_id] = orch
    return orch


def get_orchestrator(session_id: str) -> SessionOrchestrator | None:
    """Get an active orchestrator by session ID."""
    return _active_orchestrators.get(session_id)


async def end_session(session_id: str) -> None:
    """End a session — stop orchestrator and update DB."""
    orch = _active_orchestrators.pop(session_id, None)
    if orch:
        await orch.shutdown(reason="manual_end", emit_event=False)

    factory = get_session_factory()
    async with factory() as db:
        session_repo = SessionRepository(db)
        await session_repo.end_session(session_id)

    logger.info("Session {} ended", session_id)


async def finalize_ended_session(session_id: str, reason: str) -> None:
    """Finalize an already-ended runtime and persist end state."""
    orch = _active_orchestrators.pop(session_id, None)
    if orch:
        await orch.shutdown(reason=reason, emit_event=False)

    factory = get_session_factory()
    async with factory() as db:
        session_repo = SessionRepository(db)
        await session_repo.end_session(session_id)

    logger.info("Session {} finalized by runtime reason={}", session_id, reason)


async def persist_message(
    session_id: str,
    message_id: str,
    author_type: str,
    content: str,
    author_id: str | None = None,
    author_name: str | None = None,
    target_message_id: str | None = None,
) -> None:
    """Persist a message to the database."""
    factory = get_session_factory()
    async with factory() as db:
        msg_repo = MessageRepository(db)
        # Use the provided message_id by creating with explicit id
        from src.infra.db.models import MessageModel
        msg = MessageModel(
            id=message_id,
            session_id=session_id,
            author_type=author_type,
            author_id=author_id,
            author_name=author_name,
            target_message_id=target_message_id,
            content=content,
        )
        db.add(msg)
        await db.commit()


async def persist_event(
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    message_id: str | None = None,
) -> None:
    """Persist an event to the database."""
    factory = get_session_factory()
    async with factory() as db:
        event_repo = EventRepository(db)
        await event_repo.create(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            message_id=message_id,
        )
