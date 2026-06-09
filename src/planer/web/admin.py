from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from planer.adapters.db import (
    Curation,
    Presentation,
    Slot,
    build_domain_groups,
    create_round,
    create_slot,
    get_all_groups,
    get_availabilities_for_round,
    get_curations_for_round,
    get_presentations_for_round,
    get_slots_for_round,
    get_students_in_groups,
    list_rounds,
    set_all_curations_included,
    set_round_mode,
    upsert_curation,
)
from planer.adapters.submit_results import submit_presentations
from planer.config import Settings, get_settings
from planer.domain.availability import group_feasible_slots, member_declined
from planer.domain.models import Slot as DomainSlot
from planer.export import build_export_rows, rows_to_csv
from planer.logging_setup import get_logger
from planer.planning import solve_round
from planer.security import (
    csrf_tokens_match,
    generate_csrf_token,
    parse_basic_auth,
    sign_tutor_token,
    verify_admin_password,
)
from planer.web.deps import get_db

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
logger = get_logger("web.admin")

_BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Tutorium-Planer Admin"'}
_CSRF_COOKIE = "csrf_token"


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def get_admin_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> str:
    """Identify the admin caller.

    In ``password`` mode an Argon2 HTTP Basic credential is enforced. The default
    mode performs **no** app-level check: the reverse proxy / network is the real
    security boundary, so the app only reads the proxy-provided identity header
    for display/logging and falls back to "admin" if it is absent.
    """
    if settings.admin_auth_mode == "password":
        auth = request.headers.get("Authorization", "")
        creds = parse_basic_auth(auth)
        if creds is None or not verify_admin_password(creds[1], settings.admin_password_hash):
            raise HTTPException(status_code=401, headers=_BASIC_CHALLENGE)
        return creds[0]
    return request.headers.get(settings.admin_forward_auth_header, "") or "admin"


# ---------------------------------------------------------------------------
# CSRF (double-submit cookie) for state-changing admin forms
# ---------------------------------------------------------------------------


async def require_csrf(request: Request) -> None:
    """Reject POSTs whose form token does not match the CSRF cookie."""
    cookie = request.cookies.get(_CSRF_COOKIE)
    form = await request.form()
    token = form.get(_CSRF_COOKIE)
    if not csrf_tokens_match(cookie, str(token) if token is not None else None):
        logger.warning(
            "CSRF rejected",
            extra={
                "path": request.url.path,
                "client": request.client.host if request.client else "-",
            },
        )
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def _csrf_render(
    request: Request,
    template: str,
    context: dict[str, object],
    settings: Settings,
) -> HTMLResponse:
    """Render a template, ensuring a CSRF cookie + matching token in the context."""
    token = request.cookies.get(_CSRF_COOKIE) or generate_csrf_token()
    context = {**context, "csrf_token": token}
    response = templates.TemplateResponse(request, template, context)
    response.set_cookie(
        _CSRF_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )
    return response


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------


@dataclass
class GroupRow:
    group_id: str
    group_name: str
    included: bool
    pinned_slot_id: int | None
    feasible_slot_ids: set[str]
    assigned_slot_id: int | None
    is_conflict: bool
    override: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=None)
@router.get("/", response_model=None)
async def admin_index(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _admin: str = Depends(get_admin_user),
) -> RedirectResponse | HTMLResponse:
    rounds = list_rounds(session)
    if not rounds:
        return _csrf_render(request, "admin/no_rounds.html", {}, settings)
    latest = max(rounds, key=lambda r: r.id or 0)
    return RedirectResponse(url=f"/admin/rounds/{latest.id}", status_code=303)


@router.post("/rounds", response_model=None, dependencies=[Depends(require_csrf)])
async def create_new_round(
    label: str = Form(default=""),
    session: Session = Depends(get_db),
    admin: str = Depends(get_admin_user),
) -> RedirectResponse:
    rnd = create_round(session, label=label)
    logger.info("round created", extra={"round_id": rnd.id, "label": label, "admin": admin})
    return RedirectResponse(url=f"/admin/rounds/{rnd.id}", status_code=303)


@router.get("/rounds/{round_id}", response_model=None)
async def round_dashboard(
    request: Request,
    round_id: int,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _admin: str = Depends(get_admin_user),
) -> HTMLResponse:
    rounds = list_rounds(session)
    current_round = next((r for r in rounds if r.id == round_id), None)
    if current_round is None:
        raise HTTPException(status_code=404, detail="Round not found")

    slots = get_slots_for_round(session, round_id)
    domain_slots = [DomainSlot(id=str(s.id), capacity=s.capacity) for s in slots]
    base = settings.public_base_url.rstrip("/")
    tutor_links = {
        s.id: f"{base}/tutor/{sign_tutor_token(s.id, round_id, secret_key=settings.secret_key)}"
        for s in slots
        if s.id is not None
    }

    db_avails = get_availabilities_for_round(session, round_id)
    avail_dict: dict[tuple[str, str], bool] = {
        (a.student_id, str(a.slot_id)): a.available for a in db_avails
    }

    curations = {c.group_id: c for c in get_curations_for_round(session, round_id)}
    presentations = {p.group_id: p for p in get_presentations_for_round(session, round_id)}

    db_groups = get_all_groups(session)
    group_ids = [g.id for g in db_groups]
    db_students = get_students_in_groups(session, group_ids)
    domain_groups = build_domain_groups(db_groups, db_students)

    group_rows: list[GroupRow] = []
    for dg in domain_groups:
        cur: Curation | None = curations.get(dg.id)
        included = cur.included if cur else True
        pinned_slot_id = cur.pinned_slot_id if cur else None
        override = cur.override if cur else False
        feasible = group_feasible_slots(dg, avail_dict, domain_slots)
        pres: Presentation | None = presentations.get(dg.id)
        # Conflict: a slot was fixed for the group but a member declined it
        # (and the admin hasn't overridden) → must be resolved before solving.
        is_conflict = (
            pinned_slot_id is not None
            and member_declined(dg, str(pinned_slot_id), avail_dict)
            and not override
        )
        group_rows.append(
            GroupRow(
                group_id=dg.id,
                group_name=dg.name,
                included=included,
                pinned_slot_id=pinned_slot_id,
                feasible_slot_ids=feasible,
                assigned_slot_id=pres.slot_id if pres else None,
                is_conflict=is_conflict,
                override=override,
            )
        )

    solver_ran = bool(presentations)

    return _csrf_render(
        request,
        "admin/dashboard.html",
        {
            "rounds": rounds,
            "current_round": current_round,
            "slots": slots,
            "tutor_links": tutor_links,
            "group_rows": group_rows,
            "solver_ran": solver_ran,
            "mode": current_round.mode,
        },
        settings,
    )


@router.post("/rounds/{round_id}/slots", response_model=None, dependencies=[Depends(require_csrf)])
async def add_slot(
    round_id: int,
    starts_at: str = Form(...),
    room: str = Form(default=""),
    capacity: int = Form(default=5),
    session: Session = Depends(get_db),
    admin: str = Depends(get_admin_user),
) -> RedirectResponse:
    dt = datetime.fromisoformat(starts_at)
    slot = create_slot(session, round_id, dt, room=room, capacity=capacity)
    logger.info(
        "slot added",
        extra={"round_id": round_id, "slot_id": slot.id, "capacity": capacity, "admin": admin},
    )
    return RedirectResponse(url=f"/admin/rounds/{round_id}", status_code=303)


@router.post(
    "/rounds/{round_id}/slots/{slot_id}/delete",
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
async def delete_slot(
    round_id: int,
    slot_id: int,
    session: Session = Depends(get_db),
    admin: str = Depends(get_admin_user),
) -> RedirectResponse:
    slot = session.get(Slot, slot_id)
    if slot and slot.round_id == round_id:
        session.delete(slot)
        session.commit()
        logger.info(
            "slot deleted", extra={"round_id": round_id, "slot_id": slot_id, "admin": admin}
        )
    return RedirectResponse(url=f"/admin/rounds/{round_id}", status_code=303)


@router.post(
    "/rounds/{round_id}/groups/{group_id}/curate",
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
async def curate_group(
    round_id: int,
    group_id: str,
    included: str = Form(default="true"),
    pinned_slot_id: str = Form(default=""),
    override: str = Form(default=""),
    session: Session = Depends(get_db),
    admin: str = Depends(get_admin_user),
) -> RedirectResponse:
    pin: int | None = int(pinned_slot_id) if pinned_slot_id.strip() else None
    is_included = included.lower() == "true"
    is_override = override.lower() in ("true", "on", "1")
    upsert_curation(
        session,
        group_id,
        round_id,
        included=is_included,
        pinned_slot_id=pin,
        override=is_override,
    )
    logger.info(
        "group curated",
        extra={
            "round_id": round_id,
            "group_id": group_id,
            "included": is_included,
            "pinned_slot_id": pin,
            "override": is_override,
            "admin": admin,
        },
    )
    return RedirectResponse(url=f"/admin/rounds/{round_id}", status_code=303)


@router.post(
    "/rounds/{round_id}/curate-all",
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
async def curate_all(
    round_id: int,
    included: str = Form(...),
    session: Session = Depends(get_db),
    admin: str = Depends(get_admin_user),
) -> RedirectResponse:
    is_included = included.lower() == "true"
    group_ids = [g.id for g in get_all_groups(session)]
    set_all_curations_included(session, round_id, group_ids, included=is_included)
    logger.info(
        "bulk curation",
        extra={
            "round_id": round_id,
            "included": is_included,
            "count": len(group_ids),
            "admin": admin,
        },
    )
    return RedirectResponse(url=f"/admin/rounds/{round_id}", status_code=303)


@router.post(
    "/rounds/{round_id}/mode",
    response_model=None,
    dependencies=[Depends(require_csrf)],
)
async def set_mode(
    round_id: int,
    mode: str = Form(...),
    session: Session = Depends(get_db),
    admin: str = Depends(get_admin_user),
) -> RedirectResponse:
    if mode not in ("availability", "confirm"):
        raise HTTPException(status_code=400, detail="invalid mode")
    set_round_mode(session, round_id, mode)
    logger.info("round mode set", extra={"round_id": round_id, "mode": mode, "admin": admin})
    return RedirectResponse(url=f"/admin/rounds/{round_id}", status_code=303)


@router.post("/rounds/{round_id}/solve", response_model=None, dependencies=[Depends(require_csrf)])
async def run_solver(
    round_id: int,
    session: Session = Depends(get_db),
    _admin: str = Depends(get_admin_user),
) -> RedirectResponse:
    solve_round(session, round_id)
    return RedirectResponse(url=f"/admin/rounds/{round_id}", status_code=303)


@router.post("/rounds/{round_id}/sync", response_model=None, dependencies=[Depends(require_csrf)])
async def sync_results(
    round_id: int,
    dry_run: str = Form(default=""),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    admin: str = Depends(get_admin_user),
) -> RedirectResponse:
    is_dry = dry_run.lower() in ("true", "on", "1")
    try:
        report = submit_presentations(session, settings, round_id, dry_run=is_dry)
        summary = (
            f"{'Vorschau — ' if report.dry_run else ''}neu={len(report.created)} "
            f"aktualisiert={len(report.updated)} übersprungen={len(report.skipped)} "
            f"fehlgeschlagen={len(report.failed)}"
        )
    except (ValueError, PermissionError) as exc:
        summary = f"Fehler: {exc}"
        logger.warning(
            "sync failed", extra={"round_id": round_id, "admin": admin, "error": str(exc)}
        )
    return RedirectResponse(url=f"/admin/rounds/{round_id}?sync={quote(summary)}", status_code=303)


@router.get("/rounds/{round_id}/export.csv", response_model=None)
async def export_csv(
    round_id: int,
    session: Session = Depends(get_db),
    admin: str = Depends(get_admin_user),
) -> Response:
    rows = build_export_rows(session, round_id)
    csv_text = rows_to_csv(rows)
    logger.info(
        "attendance exported", extra={"round_id": round_id, "rows": len(rows), "admin": admin}
    )
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="round_{round_id}.csv"'},
    )
