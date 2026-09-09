"""Judge settings API.

Only public configuration fields are returned. The API key may be submitted for
the current process but is never echoed or persisted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, SecretStr

from app.api.deps import get_judge_settings_service
from app.services.config_service import default_config_service
from app.services.judge_settings_service import JudgeSettingsService

router = APIRouter(tags=["settings"])


class JudgeSettingsRequest(BaseModel):
    provider: str = ""
    model: str = ""
    model_version: str = ""
    base_url: str = ""
    temperature: float = Field(default=0, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    timeout: int = Field(default=30, ge=1, le=600)
    retry: int = Field(default=3, ge=1, le=10)
    api_key: SecretStr | None = None


class JudgeSettingsResponse(BaseModel):
    provider: str
    model: str
    model_version: str
    base_url: str
    temperature: float
    max_tokens: int
    timeout: int
    retry: int
    api_key_configured: bool
    configured: bool
    source: str
    reachable: bool | None = None
    message: str | None = None


@router.get("/settings/judge/persistent", response_class=PlainTextResponse)
def get_persistent_judge_config() -> PlainTextResponse:
    """Open the actual safe, persistent Judge configuration file.

    The file contains environment-variable references only. It is intentionally
    read-only from the web UI: persistent changes require editing the local
    deployment file and restarting the application.
    """
    path = default_config_service.config_dir / "system.yaml"
    content = path.read_text(encoding="utf-8") if path.is_file() else (
        "# config/system.yaml not found\n"
    )
    return PlainTextResponse(
        content,
        media_type="text/yaml",
        headers={"Content-Disposition": 'inline; filename="system-judge.yaml"'},
    )


@router.get("/settings/judge", response_model=JudgeSettingsResponse)
def get_judge_settings(
    service: JudgeSettingsService = Depends(get_judge_settings_service),
) -> JudgeSettingsResponse:
    return JudgeSettingsResponse(**service.get())


@router.put("/settings/judge", response_model=JudgeSettingsResponse)
def update_judge_settings(
    body: JudgeSettingsRequest,
    service: JudgeSettingsService = Depends(get_judge_settings_service),
) -> JudgeSettingsResponse:
    values: dict[str, Any] = body.model_dump(exclude_none=True)
    return JudgeSettingsResponse(**service.update(values))


@router.post("/settings/judge/test", response_model=JudgeSettingsResponse)
def test_judge_settings(
    service: JudgeSettingsService = Depends(get_judge_settings_service),
) -> JudgeSettingsResponse:
    return JudgeSettingsResponse(**service.test())
