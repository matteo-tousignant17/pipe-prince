# Vercel ASGI entry point — imports the FastAPI app from main.py
from main import app  # noqa: F401 — Vercel discovers `app` automatically
