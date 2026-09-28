"""RAG_INPUT_URL written only in .env.local must actually take effect.

Regression: `config/*.yaml` substitution resolves `${VAR}` against `os.environ`
only, and pydantic-settings reads `.env.local` / `.env.test` internally WITHOUT
populating `os.environ`. So `system.yaml`'s `url: ${RAG_INPUT_URL:}` always
resolved to empty on the documented local path (`uvicorn app.main:app`), and
every run silently fell back to golden replay — answering from the dataset's own
metadata — while reporting success. The same omission made
RAG_INPUT_TIMEOUT / RAG_INPUT_RETRY pure documentation.

The tests monkeypatch Settings rather than reading a real .env file, so they do
not depend on the developer's machine.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.run_planner import normalize_rag_input


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch: pytest.MonkeyPatch):
    """Neutral Settings for every test here, regardless of the real .env.local."""
    monkeypatch.setattr(settings, "rag_input_url", None, raising=False)
    monkeypatch.setattr(settings, "rag_input_timeout", None, raising=False)
    monkeypatch.setattr(settings, "rag_input_retry", None, raising=False)


def test_url_set_only_in_settings_yields_http() -> None:
    """The bug: this used to stay golden_replay."""
    settings.rag_input_url = "http://rag.example/query"

    out = normalize_rag_input({})

    assert out["mode"] == "http"
    assert out["url"] == "http://rag.example/query"


def test_nothing_configured_stays_golden_replay() -> None:
    out = normalize_rag_input({})

    assert out["mode"] == "golden_replay"
    assert out["url"] is None


def test_profile_explicit_golden_replay_is_not_overridden_by_settings() -> None:
    """The profile layer is authoritative; the existing contract is preserved."""
    settings.rag_input_url = "http://rag.example/query"

    out = normalize_rag_input({"rag_input": {"mode": "golden_replay"}})

    assert out["mode"] == "golden_replay"
    assert out["url"] is None


def test_profile_layer_url_is_not_overridden_by_settings() -> None:
    settings.rag_input_url = "http://rag.example/query"

    out = normalize_rag_input({"rag_input": {"url": "http://profile.local/query"}})

    assert out["url"] == "http://profile.local/query"


def test_timeout_and_retry_fall_back_to_settings() -> None:
    """These two were documented in .env.example but read by nothing."""
    settings.rag_input_url = "http://rag.example/query"
    settings.rag_input_timeout = 12.0
    settings.rag_input_retry = 7

    out = normalize_rag_input({})

    assert out["timeout"] == 12.0
    assert out["retry"] == 7


def test_explicit_values_beat_settings() -> None:
    settings.rag_input_timeout = 12.0
    settings.rag_input_retry = 7

    out = normalize_rag_input({"system": {"rag_input": {"timeout": 3, "retry": 1}}})

    assert out["timeout"] == 3
    assert out["retry"] == 1
