"""Centralized configuration loading from config/app.yaml + environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"


class AppConfig(BaseModel):
    name: str = "BrainstormAI"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000


class LLMConfig(BaseModel):
    default_base_url: str
    default_api_key: str
    default_model: str
    available_models: list[str] = Field(default_factory=list)
    request_timeout: int
    model_endpoints: dict[str, "ModelEndpointConfig"] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_endpoints(cls, data: Any) -> Any:
        """Support model endpoint definitions as flat llm children or in model_endpoints."""
        if not isinstance(data, dict):
            return data

        known_fields = {
            "default_base_url",
            "default_api_key",
            "base_url",
            "api_key",
            "default_model",
            "available_models",
            "request_timeout",
            "model_endpoints",
        }

        # Backward compatibility: allow old names base_url/api_key.
        if "default_base_url" not in data and "base_url" in data:
            data["default_base_url"] = data["base_url"]
        if "default_api_key" not in data and "api_key" in data:
            data["default_api_key"] = data["api_key"]

        base_url = data.get("default_base_url") or data.get("base_url")
        normalized: dict[str, dict[str, str]] = {}

        # Preferred explicit section
        explicit = data.get("model_endpoints")
        if isinstance(explicit, dict):
            for name, value in explicit.items():
                endpoint = _normalize_endpoint_item(value, base_url)
                if endpoint:
                    normalized[name] = endpoint

        # Backward compatible flat definitions under llm.*
        for key in list(data.keys()):
            if key in known_fields:
                continue
            endpoint = _normalize_endpoint_item(data[key], base_url)
            if endpoint:
                normalized[key] = endpoint
                data.pop(key, None)

        if normalized:
            data["model_endpoints"] = normalized
            if not data.get("available_models"):
                data["available_models"] = list(normalized.keys())

        return data

    def resolve_runtime(self, selected_model: str | None) -> tuple[str, str, str]:
        """Resolve final model/base_url/api_key for a selected model key."""
        target = selected_model or self.default_model
        endpoint = self.model_endpoints.get(target)
        if endpoint:
            return (
                endpoint.model,
                endpoint.api_key,
                endpoint.base_url or self.default_base_url,
            )
        return target, self.default_api_key, self.default_base_url


class ModelEndpointConfig(BaseModel):
    model: str
    api_key: str
    base_url: str | None = None


class SessionConfig(BaseModel):
    max_agents: int = Field(default=5, ge=1, le=5)
    agent_cooldown_seconds: float = 2.0
    global_speak_interval_seconds: float = 3.0
    silence_end_seconds: int = 15
    max_total_ai_messages: int = 50
    pause_timeout_seconds: int = Field(default=600, ge=0)


class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///brainstorm.db"


class Settings(BaseModel):
    app: AppConfig = AppConfig()
    llm: LLMConfig
    session: SessionConfig = SessionConfig()
    database: DatabaseConfig = DatabaseConfig()


def _normalize_endpoint_item(value: Any, fallback_base_url: str | None) -> dict[str, str] | None:
    """Normalize endpoint item from dict or single-item list shape."""
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]

    if not isinstance(value, dict):
        return None

    model = value.get("model")
    api_key = value.get("api_key")
    base_url = value.get("base_url") or fallback_base_url
    if not model or not api_key:
        return None
    normalized: dict[str, str] = {
        "model": str(model),
        "api_key": str(api_key),
    }
    if base_url:
        normalized["base_url"] = str(base_url)
    return normalized


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml_config() -> dict[str, Any]:
    """Load config/app.yaml and optionally merge config/app.local.yaml."""
    data: dict[str, Any] = {}

    base_path = _CONFIG_DIR / "app.yaml"
    if base_path.exists():
        with open(base_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    local_path = _CONFIG_DIR / "app.local.yaml"
    if local_path.exists():
        with open(local_path, "r", encoding="utf-8") as f:
            local_data = yaml.safe_load(f) or {}
            data = _deep_merge(data, local_data)

    return data


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides with BRAINSTORM_ prefix.

    Mapping: BRAINSTORM_LLM__DEFAULT_API_KEY -> data["llm"]["default_api_key"]
    """
    prefix = "BRAINSTORM_"
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        parts = env_key[len(prefix):].lower().split("__")
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = env_value

    return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings (singleton)."""
    raw = _load_yaml_config()
    raw = _apply_env_overrides(raw)
    return Settings(**raw)
