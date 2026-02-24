"""Entry point for BrainstormAI."""

import uvicorn

from src.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "src.app:app",
        host=settings.app.host,
        port=settings.app.port,
        reload=settings.app.debug,
    )


if __name__ == "__main__":
    main()
