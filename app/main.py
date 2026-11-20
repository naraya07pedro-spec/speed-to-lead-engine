"""HTTP layer. Thin on purpose: parse, authenticate, hand off to LeadService."""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import simulator
from .config import Settings
from .dashboard import render_dashboard
from .delivery import build_crm, build_notifier
from .metrics import build_report
from .security import verify_form_signature, verify_twilio
from .service import InboundLead, IntakeStatus, LeadService
from .store import Store, utcnow


class FormLead(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254)
    message: str | None = Field(default=None, max_length=4000)
    source: str = Field(default="web_form", pattern=r"^[a-z0-9_]{2,32}$")
    submission_id: str | None = Field(default=None, max_length=128)


class TouchBody(BaseModel):
    by: str = Field(default="staff", max_length=64)


def create_app(settings: Settings | None = None, service: LeadService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if service is None:
        store = Store(settings.database_path)
        service = LeadService(settings, store, build_notifier(settings), build_crm(settings))

    app = FastAPI(
        title="Speed-to-Lead Engine",
        version="0.1.0",
        description="Answers every inbound home-service lead in seconds and shows where leads leak.",
    )
    app.state.service = service

    def svc() -> LeadService:
        return app.state.service

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        return {"ok": True, "notifier": service.notifier.channel, "crm": service.crm.name}

    @app.post("/webhooks/form", tags=["intake"], status_code=202)
    async def form_webhook(
        request: Request,
        x_signature: str | None = Header(default=None),
        x_timestamp: str | None = Header(default=None),
        s: LeadService = Depends(svc),
    ) -> dict:
        raw = await request.body()
        if settings.webhook_secret:
            if not verify_form_signature(settings.webhook_secret, raw, x_signature, x_timestamp):
                raise HTTPException(status_code=401, detail="invalid signature")
        elif not settings.demo_mode:
            raise HTTPException(status_code=500, detail="WEBHOOK_SECRET must be set outside demo mode")
        try:
            body = FormLead.model_validate(json.loads(raw or b"{}"))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = s.ingest(
            InboundLead(
                source=body.source,
                name=body.name,
                phone=body.phone,
                email=body.email,
                message=body.message,
                external_id=body.submission_id,
            )
        )
        if result.status is IntakeStatus.REJECTED:
            raise HTTPException(status_code=422, detail=result.reason)
        return result.to_dict()

    @app.post("/webhooks/twilio/missed-call", tags=["intake"])
    async def twilio_missed_call(
        request: Request,
        x_twilio_signature: str | None = Header(default=None),
        s: LeadService = Depends(svc),
    ) -> PlainTextResponse:
        """Point a Twilio number's call-status or voicemail callback here."""
        params = dict(parse_qsl((await request.body()).decode()))
        if settings.verify_twilio_signature:
            url = settings.public_base_url.rstrip("/") + request.url.path
            if not verify_twilio(settings.twilio_auth_token or "", url, params, x_twilio_signature):
                raise HTTPException(status_code=401, detail="invalid twilio signature")
        status = params.get("CallStatus", "no-answer")
        if status not in {"no-answer", "busy", "failed", "canceled", "completed"}:
            return PlainTextResponse("<Response/>", media_type="application/xml")
        if status == "completed" and not params.get("RecordingUrl"):
            # An answered call is not a missed lead.
            return PlainTextResponse("<Response/>", media_type="application/xml")
        s.ingest(
            InboundLead(
                source="missed_call",
                name=params.get("CallerName"),
                phone=params.get("From"),
                message=params.get("TranscriptionText") or "missed call",
                external_id=params.get("CallSid"),
            )
        )
        return PlainTextResponse("<Response/>", media_type="application/xml")

    @app.post("/leads/{lead_id}/touch", tags=["lifecycle"])
    def touch(lead_id: int, body: TouchBody | None = None, s: LeadService = Depends(svc)) -> dict:
        """Call this when a person has actually spoken to the customer."""
        try:
            first = s.mark_touched(lead_id, by=(body.by if body else "staff"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="lead not found") from exc
        return {"lead_id": lead_id, "first_touch": first}

    @app.post("/jobs/escalate", tags=["ops"])
    def escalate(s: LeadService = Depends(svc)) -> dict:
        """Run from cron every minute. Safe to call concurrently."""
        return {"escalated": s.escalate_stale()}

    @app.get("/leads", tags=["lifecycle"])
    def leads(limit: int = Query(default=50, ge=1, le=500), s: LeadService = Depends(svc)) -> list[dict]:
        return [lead.to_dict() for lead in s.store.recent(limit)]

    @app.get("/leads/{lead_id}", tags=["lifecycle"])
    def lead_detail(lead_id: int, s: LeadService = Depends(svc)) -> dict:
        lead = s.store.get(lead_id)
        if lead is None:
            raise HTTPException(status_code=404, detail="lead not found")
        return {**lead.to_dict(), "events": s.store.events(lead_id)}

    @app.get("/metrics", tags=["reporting"])
    def metrics(days: int = Query(default=14, ge=1, le=90), s: LeadService = Depends(svc)) -> dict:
        now = utcnow()
        report = build_report(
            s.store.all_since(now - timedelta(days=days)),
            now=now,
            window_days=days,
            sla_minutes=settings.escalation_minutes,
        )
        return report.to_dict()

    @app.get("/dead-letters", tags=["ops"])
    def dead_letters(s: LeadService = Depends(svc)) -> list[dict]:
        return s.store.dead_letters()

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard(days: int = Query(default=14, ge=1, le=90), s: LeadService = Depends(svc)) -> str:
        now = utcnow()
        report = build_report(
            s.store.all_since(now - timedelta(days=days)),
            now=now,
            window_days=days,
            sla_minutes=settings.escalation_minutes,
        )
        return render_dashboard(settings, report, s.store.recent(25), now=now)

    if settings.demo_mode:

        @app.post("/demo/simulate", tags=["demo"])
        def simulate(
            count: int = Query(default=25, ge=1, le=100),
            reset: bool = False,
            s: LeadService = Depends(svc),
        ) -> dict:
            if reset:
                s.store.reset()
            return {"created": simulator.run(s, count)}

    return app
