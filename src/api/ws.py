"""WebSocket endpoint for real-time group chat streaming."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from src.domain.schemas import WSClientEvent
from src.services.orchestrator import ChatMessage
from src.services.session import (
    end_session,
    finalize_ended_session,
    get_orchestrator,
    persist_event,
    persist_message,
)

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}")
async def session_websocket(websocket: WebSocket, session_id: str) -> None:
    """Main WebSocket endpoint for a brainstorming session.

    Client sends: {"type": "user_message", "content": "..."} | {"type": "stop"} | {"type": "end_session"}
    Server sends: {"type": "message_started|message_delta|message_completed|status|error|session_ended|agents_ready", "data": {...}}
    """
    await websocket.accept()
    logger.info("WebSocket connected for session {}", session_id)

    orch = get_orchestrator(session_id)
    if not orch:
        await websocket.send_json({"type": "error", "data": {"error": "Session not found or not active"}})
        await websocket.close()
        return
    runtime_ended = False

    # Wire up the event emitter to push events via WebSocket
    async def on_event(event: dict[str, Any]) -> None:
        """Forward orchestrator events to WebSocket and persist them."""
        nonlocal runtime_ended
        try:
            await websocket.send_json(event)
        except Exception as e:
            logger.warning("Failed to send WS event: {}", e)

        # Persist events to DB
        event_type = event.get("type", "")
        data = event.get("data", {})

        try:
            if event_type == "message_completed":
                # Persist the completed message
                await persist_message(
                    session_id=session_id,
                    message_id=data.get("message_id", ""),
                    author_type="ai",
                    content=data.get("content", ""),
                    author_id=data.get("agent_id"),
                    author_name=data.get("nickname"),
                    target_message_id=data.get("target_message_id"),
                )

            # Persist all events (except high-frequency deltas — persist only start/complete)
            if event_type != "message_delta":
                await persist_event(
                    session_id=session_id,
                    event_type=event_type,
                    payload=data,
                    message_id=data.get("message_id"),
                )
            if event_type == "session_ended" and not runtime_ended:
                runtime_ended = True
                await finalize_ended_session(
                    session_id=session_id,
                    reason=data.get("reason", "session_ended"),
                )
                await websocket.close()
        except Exception as e:
            logger.error("Failed to persist event: {}", e)

    orch.on_event = on_event

    # Send agents_ready event with all agent info
    await websocket.send_json({
        "type": "agents_ready",
        "data": {
            "session_id": session_id,
            "agents": [
                {
                    "id": a.info.id,
                    "nickname": a.info.nickname,
                    "persona": a.info.persona,
                    "style": a.info.style,
                }
                for a in orch.agents
            ],
        },
    })

    # Main receive loop
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                event_data = json.loads(raw)
                client_event = WSClientEvent(**event_data)
            except (json.JSONDecodeError, Exception) as e:
                await websocket.send_json({
                    "type": "error",
                    "data": {"error": f"Invalid event format: {e}"},
                })
                continue

            if client_event.type == "user_message":
                content = (client_event.content or "").strip()
                if not content:
                    continue
                logger.info("Received user message for session {}: {}", session_id, content[:80])

                msg_id = str(uuid.uuid4())

                # Persist user message
                await persist_message(
                    session_id=session_id,
                    message_id=msg_id,
                    author_type="user",
                    content=content,
                    author_name="用户",
                )

                # Notify client
                await websocket.send_json({
                    "type": "message_completed",
                    "data": {
                        "message_id": msg_id,
                        "author_type": "user",
                        "author_name": "用户",
                        "content": content,
                    },
                })

                # Trigger AI conversation
                msg = ChatMessage(
                    id=msg_id,
                    author_type="user",
                    author_id=None,
                    author_name="用户",
                    content=content,
                )
                await orch.handle_new_message(msg)

            elif client_event.type == "stop":
                logger.info("User requested stop for session {}", session_id)
                await orch.stop(force=True)
                await websocket.send_json({
                    "type": "status",
                    "data": {"status": "generation_stopped"},
                })

            elif client_event.type == "end_session":
                logger.info("User ended session {}", session_id)
                runtime_ended = True
                await end_session(session_id)
                await websocket.send_json({
                    "type": "session_ended",
                    "data": {"session_id": session_id},
                })
                await websocket.close()
                return

            else:
                await websocket.send_json({
                    "type": "error",
                    "data": {"error": f"Unknown event type: {client_event.type}"},
                })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session {}", session_id)
    except Exception as e:
        logger.error("WebSocket error for session {}: {}", session_id, e)
        try:
            await websocket.send_json({
                "type": "error",
                "data": {"error": str(e)},
            })
        except Exception:
            pass
    finally:
        orch.on_event = None
