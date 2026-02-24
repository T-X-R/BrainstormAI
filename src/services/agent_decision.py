"""Agent decision module extracted from orchestrator."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.config.settings import get_settings
from src.domain.schemas import AgentAction, AgentDecision, AgentInfo
from src.infra.llm.factory import create_chat_agent
from src.infra.llm.token_usage import create_token_usage_callback
from src.infra.prompts.loader import render_prompt
from src.utils.common import current_time_str, is_transient_timeout_error

def build_decision_prompt(
    *,
    nickname: str,
    persona: str,
    style: str,
    topic: str,
    recent_messages: list[dict[str, Any]],
    last_speaker_name: str | None,
    cooldown_active: bool,
) -> str:
    return render_prompt(
        "agent_decision.md",
        nickname=nickname,
        persona=persona,
        style=style,
        topic=topic,
        CURRENT_TIME=current_time_str(),
        recent_messages=recent_messages,
        last_speaker_name=last_speaker_name,
        cooldown_active=cooldown_active,
    )


async def decide_agent_action(
    *,
    agent_info: AgentInfo,
    topic: str,
    recent_messages: list[dict[str, Any]],
    last_speaker_name: str | None,
    cooldown_active: bool,
    decision_semaphore: asyncio.Semaphore | None,
) -> AgentDecision:
    decision = AgentDecision(action=AgentAction.SILENT)
    settings = get_settings()
    default_model_name = settings.llm.default_model
    active_model_name = agent_info.model_name or default_model_name
    prompt_text = build_decision_prompt(
        nickname=agent_info.nickname,
        persona=agent_info.persona,
        style=agent_info.style,
        topic=topic,
        recent_messages=recent_messages,
        last_speaker_name=last_speaker_name,
        cooldown_active=cooldown_active,
    )
    agent = create_chat_agent(
        model_name=active_model_name,
        temperature=0.7,
        streaming=False,
        response_format=AgentDecision,
    )
    max_attempts = 3
    semaphore = decision_semaphore or asyncio.Semaphore(2)

    for attempt in range(max_attempts):
        try:
            async with semaphore:
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": prompt_text}]},
                    config={
                        "callbacks": [
                            create_token_usage_callback(
                                stage="agent_decision",
                                fallback_model_name=active_model_name,
                            )
                        ]
                    },
                )
            structured = result.get("structured_response")
            if not isinstance(structured, AgentDecision):
                raise ValueError("Missing structured_response for AgentDecision")
            decision = structured
            break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            is_timeout = is_transient_timeout_error(exc)
            if is_timeout and attempt < max_attempts - 1:
                delay_seconds = 0.8 * (attempt + 1)
                logger.warning(
                    "Agent {} decision timeout on attempt {}/{}. Retrying in {:.1f}s: {}",
                    agent_info.nickname,
                    attempt + 1,
                    max_attempts,
                    delay_seconds,
                    exc,
                )
                await asyncio.sleep(delay_seconds)
                continue
            logger.warning(
                "Agent {} structured decision failed: {}. Defaulting to silent.",
                agent_info.nickname,
                exc,
            )
            break
    return decision
