"""Jinja2 template loader and renderer for prompt management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Template
from loguru import logger

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# Jinja2 environment — loaded once, cached
_env: Environment | None = None


def _get_env() -> Environment:
    """Get or create the Jinja2 environment."""
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_PROMPTS_DIR)),
            autoescape=False,  # Prompts are plain text, not HTML
            trim_blocks=True,
            lstrip_blocks=True,
        )
        logger.debug("Jinja2 environment initialized from {}", _PROMPTS_DIR)
    return _env


def get_template(name: str) -> Template:
    """Load a template by filename (e.g. 'persona_generation.md')."""
    return _get_env().get_template(name)


def render_prompt(template_name: str, **kwargs: Any) -> str:
    """Render a prompt template with the given variables.

    Args:
        template_name: The template filename (e.g. 'persona_generation.md').
        **kwargs: Template variables.

    Returns:
        The rendered prompt string.
    """
    template = get_template(template_name)
    rendered = template.render(**kwargs)
    logger.trace("Rendered prompt '{}': {} chars", template_name, len(rendered))
    return rendered
