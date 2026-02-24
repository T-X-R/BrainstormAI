"""LLM token usage callback helpers."""

from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from loguru import logger


class TokenUsageCallback(BaseCallbackHandler):
    """Logs token usage reported by provider at the end of an LLM call."""

    def __init__(self, stage: str, fallback_model_name: str | None = None) -> None:
        self.stage = stage
        self.fallback_model_name = fallback_model_name or "unknown"

    @staticmethod
    def _extract_usage(response: LLMResult) -> tuple[int, int, int, str | None]:
        usage_dict: dict | None = None
        model_name: str | None = None

        if isinstance(response.llm_output, dict):
            usage = response.llm_output.get("token_usage")
            if isinstance(usage, dict):
                usage_dict = usage
            llm_output_model = response.llm_output.get("model_name")
            if isinstance(llm_output_model, str):
                model_name = llm_output_model

        if usage_dict is None and response.generations:
            first_generation = response.generations[0][0]
            message = getattr(first_generation, "message", None)
            usage_meta = getattr(message, "usage_metadata", None)
            if isinstance(usage_meta, dict):
                usage_dict = usage_meta
            response_meta = getattr(message, "response_metadata", None)
            if isinstance(response_meta, dict):
                response_model = response_meta.get("model_name")
                if isinstance(response_model, str):
                    model_name = response_model

        usage_dict = usage_dict or {}
        prompt_tokens = int(
            usage_dict.get("prompt_tokens")
            or usage_dict.get("input_tokens")
            or 0
        )
        completion_tokens = int(
            usage_dict.get("completion_tokens")
            or usage_dict.get("output_tokens")
            or 0
        )
        total_tokens = int(
            usage_dict.get("total_tokens")
            or (prompt_tokens + completion_tokens)
        )
        return prompt_tokens, completion_tokens, total_tokens, model_name

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        prompt_tokens, completion_tokens, total_tokens, model_name = self._extract_usage(response)
        logger.info(
            "LLM token usage | stage={} model={} prompt_tokens={} completion_tokens={} total_tokens={}",
            self.stage,
            model_name or self.fallback_model_name,
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )


def create_token_usage_callback(stage: str, fallback_model_name: str | None = None) -> TokenUsageCallback:
    return TokenUsageCallback(stage=stage, fallback_model_name=fallback_model_name)
