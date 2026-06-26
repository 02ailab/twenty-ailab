# Liveness/readiness endpoints (excluded from request logging by convention).
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import db

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    try:
        ok = await db.ping()
    except Exception:
        ok = False
    if ok:
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "not-ready"}, status_code=503)
