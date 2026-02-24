"""Persona generation service — creates unique AI personas at session start."""

from loguru import logger

from src.config.settings import get_settings
from src.domain.schemas import PersonaBatch
from src.infra.llm.factory import create_chat_agent
from src.infra.llm.token_usage import create_token_usage_callback
from src.infra.prompts.loader import render_prompt
from src.utils.common import current_time_str


def _validate_persona_batch(personas: list[dict[str, str]], expected_count: int) -> None:
    if len(personas) != expected_count:
        raise ValueError(f"Expected {expected_count} personas, got {len(personas)}")

    nickname_set: set[str] = set()
    for idx, persona in enumerate(personas, start=1):
        nickname = (persona.get("nickname") or "").strip()
        style = (persona.get("style") or "").strip()
        description = (persona.get("persona") or "").strip()
        if not nickname or not style or not description:
            raise ValueError(f"Persona #{idx} has empty required fields")
        if len(nickname) > 6:
            raise ValueError(f"Persona #{idx} nickname exceeds 6 chars: {nickname}")
        if nickname in nickname_set:
            raise ValueError(f"Duplicate nickname detected: {nickname}")
        nickname_set.add(nickname)


async def generate_personas(
    topic: str,
    agent_count: int,
    model_name: str | None = None,
) -> list[dict[str, str]]:
    """Generate all personas in one request for better global diversity."""
    prompt_text = render_prompt(
        "persona_generation.md",
        CURRENT_TIME=current_time_str(),
        topic=topic,
        agent_count=agent_count,
    )

    try:
        agent = create_chat_agent(
            model_name=model_name,
            temperature=0.95,
            streaming=False,
            response_format=PersonaBatch,
        )
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt_text}]},
            config={
                "callbacks": [
                    create_token_usage_callback(
                        stage="persona_generation",
                        fallback_model_name=model_name or get_settings().llm.default_model,
                    )
                ]
            },
        )
        structured = result.get("structured_response")
        if not isinstance(structured, PersonaBatch):
            raise ValueError("Missing structured_response for PersonaBatch")
        personas = [item.model_dump() for item in structured.personas]
        _validate_persona_batch(personas, expected_count=agent_count)
    except Exception as exc:
        logger.error("Persona batch generation failed: {}", exc)
        raise RuntimeError("Persona generation failed") from exc

    logger.info(
        "Generated {} personas: {}",
        len(personas),
        [item.get("nickname") for item in personas],
    )
    return personas
