"""Agent reply generation module extracted from orchestrator."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable

from loguru import logger

from src.config.settings import get_settings
from src.domain.schemas import AgentAction, AgentDecision, AgentInfo
from src.infra.llm.factory import create_chat_agent
from src.infra.llm.token_usage import create_token_usage_callback
from src.infra.prompts.loader import render_prompt
from src.utils.common import current_time_str, is_transient_timeout_error


def _consume_leading_duplicate_mention(
    buffered_text: str,
    target_name: str,
) -> tuple[bool, str]:
    """Remove a model-emitted duplicated mention prefix at stream start."""
    trimmed = buffered_text.lstrip()
    if not trimmed:
        return False, ""

    expected = f"@{target_name}"
    if expected.startswith(trimmed) and len(trimmed) < len(expected):
        # Still potentially a split mention token, wait for more content.
        return False, ""

    if trimmed.startswith(expected):
        remainder = trimmed[len(expected):].lstrip()
        if remainder[:1] in {",", "，", ":", "："}:
            remainder = remainder[1:].lstrip()
        return True, remainder

    return True, buffered_text


def _extract_token_text(token: Any) -> str:
    if isinstance(token, str):
        return token
    content = getattr(token, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    content_blocks = getattr(token, "content_blocks", None)
    if isinstance(content_blocks, list):
        parts = []
        for block in content_blocks:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    text_method = getattr(token, "text", None)
    if callable(text_method):
        try:
            value = text_method()
            if isinstance(value, str):
                return value
        except Exception:
            pass
    return ""


async def generate_agent_reply(
    *,
    info: AgentInfo,
    decision: AgentDecision,
    topic: str,
    recent_messages: list[dict[str, Any]],
    target_message: dict[str, Any] | None,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
    mark_output_started: Callable[[], None],
) -> tuple[str, str]:
    action_description = "向群组分享你的想法"
    if decision.action == AgentAction.REPLY_USER:
        action_description = "回复用户"
    elif decision.action == AgentAction.REPLY_AI:
        action_description = f"回复 {decision.target_author_name or '另一位 AI'}"
    elif decision.action == AgentAction.COMMENT:
        action_description = f"评论 {decision.target_author_name or '某位成员'} 的观点"

    prompt_text = render_prompt(
        "agent_reply.md",
        nickname=info.nickname,
        persona=info.persona,
        style=info.style,
        CURRENT_TIME=current_time_str(),
        topic=topic,
        recent_messages=recent_messages,
        target_message=target_message,
        action_description=action_description,
        key_points=decision.key_points,
        stance=decision.stance,
    )

    agent = create_chat_agent(
        model_name=info.model_name,
        temperature=0.85,
        streaming=True,
    )

    message_id = str(uuid.uuid4())
    await emit(
        "message_started",
        {
            "message_id": message_id,
            "author_type": "ai",
            "agent_id": info.id,
            "nickname": info.nickname,
            "target_message_id": decision.target_message_id,
            "target_author_name": decision.target_author_name,
            "action": decision.action.value,
        },
    )

    mention_prefix = ""
    mention_target_name = ""
    if decision.action == AgentAction.REPLY_AI and decision.target_author_name:
        mention_target_name = decision.target_author_name
        mention_prefix = f"@{mention_target_name} "

    full_content = mention_prefix
    if mention_prefix:
        mark_output_started()
        await emit(
            "message_delta",
            {
                "message_id": message_id,
                "agent_id": info.id,
                "token": mention_prefix,
            },
        )

    token_usage_callback = create_token_usage_callback(
        stage="agent_reply",
        fallback_model_name=info.model_name or get_settings().llm.default_model,
    )
    mention_guard_resolved = not bool(mention_target_name)
    mention_guard_buffer = ""
    try:
        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                async for item in agent.astream(
                    {"messages": [{"role": "user", "content": prompt_text}]},
                    config={"callbacks": [token_usage_callback]},
                    stream_mode="messages",
                ):
                    token_obj = item[0] if isinstance(item, tuple) else item
                    token = _extract_token_text(token_obj)
                    if token:
                        token_to_emit = token
                        if not mention_guard_resolved and mention_target_name:
                            mention_guard_buffer += token
                            mention_guard_resolved, token_to_emit = _consume_leading_duplicate_mention(
                                mention_guard_buffer,
                                mention_target_name,
                            )
                            if not mention_guard_resolved:
                                continue

                        mark_output_started()
                        if token_to_emit:
                            full_content += token_to_emit
                            await emit(
                                "message_delta",
                                {
                                    "message_id": message_id,
                                    "agent_id": info.id,
                                    "token": token_to_emit,
                                },
                            )
                if not mention_guard_resolved and mention_guard_buffer:
                    # Stream ended before we could conclusively match a full mention.
                    mark_output_started()
                    full_content += mention_guard_buffer
                    await emit(
                        "message_delta",
                        {
                            "message_id": message_id,
                            "agent_id": info.id,
                            "token": mention_guard_buffer,
                        },
                    )
                    mention_guard_resolved = True
                break
            except Exception as exc:
                is_timeout = is_transient_timeout_error(exc)
                no_output_yet = not full_content.strip()
                if is_timeout and no_output_yet and attempt < max_attempts - 1:
                    delay_seconds = 1.0
                    logger.warning(
                        "Agent {} reply timeout on attempt {}/{}. Retrying in {:.1f}s: {}",
                        info.nickname,
                        attempt + 1,
                        max_attempts,
                        delay_seconds,
                        exc,
                    )
                    await asyncio.sleep(delay_seconds)
                    continue
                raise
    except asyncio.CancelledError:
        logger.info("Generation cancelled for agent {}", info.nickname)
        await emit(
            "message_cancelled",
            {
                "message_id": message_id,
                "author_type": "ai",
                "agent_id": info.id,
                "nickname": info.nickname,
                "content": full_content,
            },
        )
        raise
    except Exception as exc:
        logger.error("Generation error for agent {}: {}", info.nickname, exc)
        await emit(
            "error",
            {"message_id": message_id, "agent_id": info.id, "error": str(exc)},
        )

    return message_id, full_content
