from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlmodel import Session

from planer.adapters.db import (
    Slot,
    Student,
    get_availabilities_for_round,
    get_curation,
    get_round,
    get_slots_for_round,
    upsert_availability,
)
from planer.config import Settings, get_settings
from planer.logging_setup import get_logger
from planer.security import BadTokenError, verify_availability_token
from planer.web.deps import get_db

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
logger = get_logger("web.public")


@router.get("/availability/{token}", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def availability_form(
    request: Request,
    token: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    try:
        student_id, round_id = verify_availability_token(
            token,
            secret_key=settings.secret_key,
            max_age=settings.token_max_age_hours * 3600,
        )
    except BadTokenError:
        logger.warning("invalid availability token on GET")
        return HTMLResponse("Ungültiger oder abgelaufener Link.", status_code=400)

    rnd = get_round(session, round_id)
    mode = rnd.mode if rnd else "availability"
    existing = {
        a.slot_id: a.available
        for a in get_availabilities_for_round(session, round_id)
        if a.student_id == student_id
    }

    if mode == "confirm":
        slot = _confirm_slot(session, student_id, round_id)
        slots = [slot] if slot is not None else []
    else:
        slots = get_slots_for_round(session, round_id)

    return templates.TemplateResponse(
        request,
        "availability.html",
        {"slots": slots, "existing": existing, "token": token, "mode": mode},
    )


def _confirm_slot(session: Session, student_id: str, round_id: int) -> Slot | None:
    """The single slot pinned to this student's group (confirm mode), or None."""
    student = session.get(Student, student_id)
    if student is None:
        return None
    group_id = student.group_id or student.id  # solo students group under their own id
    curation = get_curation(session, round_id, group_id)
    if curation is None or curation.pinned_slot_id is None:
        return None
    return session.get(Slot, curation.pinned_slot_id)


@router.post("/availability/{token}", response_class=HTMLResponse)
@limiter.limit("15/minute")
async def submit_availability(
    request: Request,
    token: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    try:
        student_id, round_id = verify_availability_token(
            token,
            secret_key=settings.secret_key,
            max_age=settings.token_max_age_hours * 3600,
        )
    except BadTokenError:
        logger.warning("invalid availability token on submit")
        return HTMLResponse("Ungültiger oder abgelaufener Link.", status_code=400)

    form = await request.form()
    rnd = get_round(session, round_id)
    mode = rnd.mode if rnd else "availability"

    if mode == "confirm":
        slot = _confirm_slot(session, student_id, round_id)
        if slot is not None and slot.id is not None:
            coming = form.get("confirm") == "yes"
            upsert_availability(session, student_id, slot.id, coming)
            logger.info(
                "slot confirmed",
                extra={
                    "student_id": student_id,
                    "round_id": round_id,
                    "slot_id": slot.id,
                    "coming": coming,
                },
            )
        return templates.TemplateResponse(request, "availability_done.html", {})

    slots = get_slots_for_round(session, round_id)
    available_count = 0
    for slot in slots:
        available = f"slot_{slot.id}" in form
        assert slot.id is not None
        upsert_availability(session, student_id, slot.id, available)
        available_count += int(available)

    logger.info(
        "availability submitted",
        extra={
            "student_id": student_id,
            "round_id": round_id,
            "slots": len(slots),
            "available": available_count,
        },
    )
    return templates.TemplateResponse(request, "availability_done.html", {})
