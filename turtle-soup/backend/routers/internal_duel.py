"""Loopback-only CedarToy bridge from CedarDuet to the shared NPC pool."""

from __future__ import annotations

import asyncio
import hmac
import os
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from judge import npc_chat


router = APIRouter()


class BridgeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class DuelNpcBridgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[BridgeMessage] = Field(min_length=1, max_length=20)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    timeout: float = Field(default=20, ge=1, le=60)


def _authorize(authorization: str | None) -> None:
    expected = os.getenv("DUEL_NPC_BRIDGE_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="NPC bridge 未配置")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.strip(), expected
    ):
        raise HTTPException(status_code=401, detail="NPC bridge 鉴权失败")


@router.post("/internal/duel/npc-decision")
async def duel_npc_decision(
    body: DuelNpcBridgeBody,
    authorization: str | None = Header(default=None),
):
    _authorize(authorization)
    messages = [message.model_dump() for message in body.messages]
    try:
        content = await asyncio.wait_for(
            npc_chat(
                messages,
                max_tokens=body.max_tokens,
                timeout=body.timeout,
            ),
            timeout=body.timeout,
        )
    except HTTPException:
        raise
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="NPC bridge 请求超时") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="NPC bridge 请求格式无效") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="NPC bridge 上游失败") from exc
    if not isinstance(content, str) or not content.strip() or len(content) > 10000:
        raise HTTPException(status_code=502, detail="NPC bridge 上游响应无效")
    return {"content": content}
