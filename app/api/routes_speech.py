"""Speech route: POST /api/speech.

The browser sends the narration it just displayed. ElevenLabs is called here
so the API key never reaches the page. A failure is this request's problem;
the turn that produced the text has already succeeded.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.schemas import SpeechRequest
from app.services.speech import SpeechError, SpeechUnavailable, synthesize

router = APIRouter(tags=["speech"])


@router.post("/api/speech")
def speak(body: SpeechRequest) -> Response:
    try:
        audio = synthesize(body.text)
    except SpeechUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except SpeechError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
