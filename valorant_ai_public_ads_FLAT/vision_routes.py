from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cheat_verifier import verify_cheat_suspicion
from combat_verifier import verify_combat_events
from valorant_reference import catalog_payload
from vision_learning import (
    decode_data_url,
    model_status,
    predict_image,
    train_sample,
    vision_learning_is_configured,
)


class VisionPredictRequest(BaseModel):
    target_type: str = Field(pattern="^(agent|skill)$")
    image_data: str = Field(min_length=20, max_length=3_000_000)
    agent_label: str = Field(default="", max_length=60)


class VisionTrainRequest(BaseModel):
    target_type: str = Field(pattern="^(agent|skill)$")
    image_data: str = Field(min_length=20, max_length=3_000_000)
    label: str = Field(min_length=1, max_length=60)
    analysis_id: str = Field(min_length=1, max_length=100)
    event_index: int = Field(default=-1, ge=-1, le=100)
    predicted_label: str = Field(default="", max_length=60)
    agent_label: str = Field(default="", max_length=60)


class CombatEventRequest(BaseModel):
    event_index: int = Field(ge=0, le=20)
    timestamp: str = Field(default="", max_length=20)
    observation: str = Field(default="", max_length=1000)
    feedback: str = Field(default="", max_length=1400)
    dense_motion_strip: bool = False
    frames: list[str] = Field(min_length=2, max_length=3)


class CombatVerifyRequest(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=100)
    summary: str = Field(default="", max_length=3000)
    player_agent: str = Field(default="", max_length=80)
    events: list[CombatEventRequest] = Field(min_length=1, max_length=6)


def build_vision_router(get_auth_context, attach_refreshed_session, public_base_url: str) -> APIRouter:
    router = APIRouter()

    async def auth_for(request: Request):
        return await asyncio.to_thread(get_auth_context, request)

    def base_url(request: Request) -> str:
        return (public_base_url or str(request.base_url).rstrip("/")).rstrip("/")

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin", "").rstrip("/")
        if not origin:
            return True
        return hmac.compare_digest(origin, base_url(request))

    @router.get("/vision/status")
    async def vision_status(request: Request):
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        try:
            status = await asyncio.to_thread(model_status)
            response = JSONResponse(status)
            return attach_refreshed_session(response, auth)
        except Exception as exc:
            print(f"[VisionLearning] status error: {type(exc).__name__}: {exc}")
            return JSONResponse({"configured": False, "detail": "학습 모델 상태를 읽지 못했습니다."}, status_code=503)

    @router.get("/vision/catalog")
    async def vision_catalog(request: Request, agent: str = ""):
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        try:
            payload = await asyncio.to_thread(catalog_payload, agent[:60])
            response = JSONResponse(payload)
            return attach_refreshed_session(response, auth)
        except Exception as exc:
            print(f"[ValorantReference] catalog error: {type(exc).__name__}: {exc}")
            return JSONResponse({"ready": False, "agent": None, "abilities": []}, status_code=200)

    @router.post("/vision/predict")
    async def vision_predict(request: Request, payload: VisionPredictRequest):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)

        try:
            image_bytes = decode_data_url(payload.image_data)
            result = await asyncio.to_thread(
                predict_image,
                payload.target_type,
                image_bytes,
                payload.agent_label,
            )
            response = JSONResponse(result)
            return attach_refreshed_session(response, auth)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except Exception as exc:
            print(f"[VisionLearning] prediction error: {type(exc).__name__}: {exc}")
            return JSONResponse({"ready": False, "reason": "prediction_error"}, status_code=200)

    @router.post("/vision/combat-verify")
    async def combat_verify(request: Request, payload: CombatVerifyRequest):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)

        try:
            decoded_events = []
            total_bytes = 0
            for event in payload.events:
                frames = []
                for image_data in event.frames[:3]:
                    raw = decode_data_url(image_data)
                    total_bytes += len(raw)
                    if total_bytes > 10 * 1024 * 1024:
                        return JSONResponse({"detail": "전투 검증 이미지가 너무 큽니다."}, status_code=413)
                    frames.append(raw)
                decoded_events.append({
                    "event_index": event.event_index,
                    "timestamp": event.timestamp,
                    "observation": event.observation,
                    "feedback": event.feedback,
                    "dense_motion_strip": bool(event.dense_motion_strip),
                    "frames": frames,
                })

            result = await asyncio.to_thread(
                verify_combat_events,
                decoded_events,
                payload.summary,
                payload.player_agent,
            )

            # Cheat suspicion is intentionally verified in a second, dedicated
            # low-cost model call. Mixing HUD/kill/agent tasks with cheat
            # detection made the latter too conservative and easy to miss.
            try:
                dedicated_cheat = await asyncio.to_thread(
                    verify_cheat_suspicion,
                    decoded_events,
                )
                result["cheat_assessment"] = dedicated_cheat
                result["cheat_suspicion_verification"] = True
                result["dedicated_cheat_verifier"] = True
                result["cheat_verification_model"] = dedicated_cheat.get("model_used", "")
                result["cheat_verification_token_usage"] = dedicated_cheat.get("token_usage", {})
            except Exception as exc:
                # Keep the first-pass assessment rather than failing the whole
                # analysis if the dedicated verifier is temporarily unavailable.
                print(f"[CheatVerifier] dedicated verification skipped: {type(exc).__name__}: {exc}")
                result["dedicated_cheat_verifier"] = False
                result["cheat_verification_error"] = type(exc).__name__

            response = JSONResponse(result)
            return attach_refreshed_session(response, auth)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except Exception as exc:
            print(f"[CombatVerifier] route error: {type(exc).__name__}: {exc}")
            return JSONResponse({
                "verified": False,
                "events": [],
                "replace_summary": False,
                "corrected_summary": payload.summary,
                "reason": "combat_verification_failed",
            }, status_code=200)

    @router.post("/vision/train")
    async def vision_train(request: Request, payload: VisionTrainRequest):
        if not same_origin(request):
            return JSONResponse({"detail": "잘못된 요청 출처입니다."}, status_code=403)
        auth = await auth_for(request)
        if not auth:
            return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        if not vision_learning_is_configured():
            return JSONResponse(
                {"detail": "학습 데이터베이스가 설정되지 않았습니다. supabase_vision_learning.sql을 실행해 주세요."},
                status_code=503,
            )

        try:
            image_bytes = decode_data_url(payload.image_data)
            result = await asyncio.to_thread(
                train_sample,
                user_id=auth.user_id,
                analysis_id=payload.analysis_id,
                target_type=payload.target_type,
                event_index=payload.event_index,
                label=payload.label,
                image_bytes=image_bytes,
                predicted_label=payload.predicted_label,
                agent_label=payload.agent_label,
            )
            response = JSONResponse(result)
            return attach_refreshed_session(response, auth)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except Exception as exc:
            print(f"[VisionLearning] training error: {type(exc).__name__}: {exc}")
            return JSONResponse(
                {"detail": "학습에 실패했습니다. 최신 supabase_vision_learning.sql 설정을 확인해 주세요."},
                status_code=503,
            )

    return router
