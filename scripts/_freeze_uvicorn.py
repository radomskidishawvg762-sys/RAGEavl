"""Freeze Gate launcher: uvicorn with .env.local loaded (real Supabase + Judge)."""

from dotenv import load_dotenv

load_dotenv(".env.local")

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="warning")
