"""Tutor attendance-marking flow, magic-link authenticated.

A tutor receives a signed per-slot link. GET renders the groups
scheduled for that slot; POST marks each as PRESENTED or NO_SHOW. No admin auth
— the signed token is the credential. Bad/expired tokens → 400 without leak.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from planer.adapters.db import (
    PresentationStatus,
    get_all_groups,
    get_presentations_for_slot,
    get_students_in_groups,
    update_presentation_status,
)
from planer.adapters.db import Slot as DbSlot
from planer.config import Settings, get_settings
from planer.logging_setup import get_logger
from planer.security import BadTokenError, verify_tutor_token
from planer.web.deps import get_db
from planer.web.public import limiter

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
logger = get_logger("web.tutor")

_BAD = "Ungültiger oder abgelaufener Link."


def _load(token: str, settings: Settings) -> tuple[int, int] | None:
    try:
        return verify_tutor_token(
            token,
            secret_key=settings.secret_key,
            max_age=settings.token_max_age_hours * 3600,
        )
    except BadTokenError:
        return None


@router.get("/tutor/{token}", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def tutor_form(
    request: Request,
    token: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    parsed = _load(token, settings)
    if parsed is None:
        return HTMLResponse(_BAD, status_code=400)
    slot_id, round_id = parsed

    slot = session.get(DbSlot, slot_id)
    presentations = get_presentations_for_slot(session, round_id, slot_id)
    group_names = {g.id: g.name for g in get_all_groups(session)}
    members_by_group: dict[str, list[str]] = {}
    for student in get_students_in_groups(session, [p.group_id for p in presentations]):
        members_by_group.setdefault(student.group_id, []).append(student.name)

    rows = [
        {
            "group_id": p.group_id,
            "group_name": group_names.get(p.group_id, p.group_id),
            "members": members_by_group.get(p.group_id, []),
            "status": p.status,
        }
        for p in presentations
    ]
    return templates.TemplateResponse(
        request,
        "tutor.html",
        {"slot": slot, "rows": rows, "token": token},
    )


@router.post("/tutor/{token}", response_class=HTMLResponse)
@limiter.limit("15/minute")
async def tutor_submit(
    request: Request,
    token: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    parsed = _load(token, settings)
    if parsed is None:
        logger.warning("invalid tutor token on submit")
        return HTMLResponse(_BAD, status_code=400)
    slot_id, round_id = parsed

    form = await request.form()
    presentations = get_presentations_for_slot(session, round_id, slot_id)
    marked = 0
    for p in presentations:
        raw = form.get(f"status_{p.group_id}")
        if raw in (PresentationStatus.PRESENTED, PresentationStatus.NO_SHOW):
            update_presentation_status(session, p.group_id, slot_id, PresentationStatus(raw))
            logger.info(
                "attendance marked",
                extra={
                    "group_id": p.group_id,
                    "slot_id": slot_id,
                    "round_id": round_id,
                    "status": str(raw),
                },
            )
            marked += 1
    logger.info(
        "tutor submission", extra={"slot_id": slot_id, "round_id": round_id, "marked": marked}
    )
    return templates.TemplateResponse(request, "tutor_done.html", {})
