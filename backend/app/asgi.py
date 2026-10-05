"""ASGI entry point for uvicorn. Loads settings from environment before creating the app."""

from app.main import create_app

app = create_app()
