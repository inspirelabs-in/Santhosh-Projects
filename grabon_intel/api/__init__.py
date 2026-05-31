"""FastAPI gateway. Mounts auth middleware + routers."""
from .main import create_app

__all__ = ["create_app"]
