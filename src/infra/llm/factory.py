"""LLM + Agent factory helpers."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from loguru import logger

from src.config.settings import get_settings


def create_chat_model(
    model_name: str | None = None,
    *,
    temperature: float,
    streaming: bool = True,
) -> ChatOpenAI:
    """Create a ChatOpenAI instance using the centralized config."""
    settings = get_settings()
    llm_config = settings.llm
    resolved_model, resolved_api_key, resolved_base_url = llm_config.resolve_runtime(model_name)

    model = ChatOpenAI(
        model=resolved_model,
        base_url=resolved_base_url,
        api_key=resolved_api_key,
        temperature=temperature,
        streaming=streaming,
        request_timeout=llm_config.request_timeout,
    )

    logger.debug(
        "Created ChatOpenAI: model={}, base_url={}, streaming={}",
        model.model_name,
        resolved_base_url,
        streaming,
    )
    return model


def create_chat_agent(
    model_name: str | None = None,
    *,
    temperature: float,
    streaming: bool = True,
    response_format: Any | None = None,
):
    """Create a LangChain create_agent runnable with centralized model config."""
    model = create_chat_model(
        model_name=model_name,
        temperature=temperature,
        streaming=streaming,
    )

    agent_kwargs: dict[str, Any] = {
        "model": model,
        "tools": [],
    }
    if response_format is not None:
        agent_kwargs["response_format"] = response_format

    return create_agent(**agent_kwargs)
