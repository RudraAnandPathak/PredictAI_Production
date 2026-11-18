"""Run the API for local development: ``python -m app`` (from the backend directory)."""
import uvicorn

from .core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
        access_log=False,  # the request middleware logs one line per request, without query strings
    )


if __name__ == "__main__":
    main()
