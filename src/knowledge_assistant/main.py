"""ASGI entry point. Application composition belongs to api.app."""

from .api.app import create_app

app = create_app()
