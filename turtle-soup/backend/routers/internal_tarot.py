"""Loopback-only bridge from CedarToy Tarot sessions to the Tarot pool."""

from __future__ import annotations

import asyncio
import hmac
import os
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from judge import get_tarot_model_runtime_statuses, tarot_reading_chat


router = APIRouter()
TAROT_FLASH_MODEL = "gemini-3.5-flash"
TAROT_PRO_MODEL = "gemini-3.1-pro-preview"


class TarotMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)


class TarotBridgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[TarotMessage] = Field(min_length=1, max_length=4)
    model: Literal["gemini-3.5-flash", "gemini-3.1-pro-preview"] = TAROT_FLASH_MODEL
    max_tokens: int = Field(default=4096, ge=1, le=8192)
    timeout: float = Field(default=90, ge=5, le=120)


def _authorize(authorization: str | None) -> None:
    expected = os.getenv("TAROT_BRIDGE_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Tarot bridge 未配置")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.strip(), expected
    ):
        raise HTTPException(status_code=401, detail="Tarot bridge 鉴权失败")


@router.get("/internal/tarot/models/status")
async def tarot_model_statuses(
    authorization: str | None = Header(default=None),
):
    _authorize(authorization)
    return await get_tarot_model_runtime_statuses()


@router.post("/internal/tarot/reading")
async def tarot_reading(
    body: TarotBridgeBody,
    authorization: str | None = Header(default=None),
):
    _authorize(authorization)
    messages = [message.model_dump() for message in body.messages]
    if sum(len(message["content"]) for message in messages) > 60_000:
        raise HTTPException(status_code=422, detail="Tarot bridge 请求格式无效")
    try:
        content = await asyncio.wait_for(
            tarot_reading_chat(
                messages,
                model=body.model,
                max_tokens=body.max_tokens,
                timeout=body.timeout,
            ),
            timeout=body.timeout + 1,
        )
    except HTTPException:
        raise
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Tarot bridge 请求超时") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Tarot bridge 请求格式无效") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Tarot bridge 上游失败") from exc
    if not isinstance(content, str) or not content.strip() or len(content) > 100_000:
        raise HTTPException(status_code=502, detail="Tarot bridge 上游响应无效")
    return {
        "content": content,
        "source": "tarot-ritual",
        "pool": "tarot",
        "model": body.model,
    }
