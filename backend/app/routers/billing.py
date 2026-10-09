"""Billing router — plans, subscription, invoices, usage, payment, contact.

All routes workspace-scoped, authenticated. Plans are static config; subscription
is per-workspace (owner/administrator can change). Invoices are generated per
billing period. Usage is computed live from locations/forecasts. No external
Stripe call in dev — payment is stored as last4/brand.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.billing import (
    BillingContact,
    BillingInvoice,
    BillingPaymentMethod,
    BillingSubscription,
)
from app.models.forecast import Forecast
from app.models.location import Location
from app.models.user import User
from app.security.permissions import BILLING_MANAGE, has_capability

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static plans — mirrors frontend/src/pages/Billing.tsx:22
# ---------------------------------------------------------------------------

PLANS: list[dict[str, Any]] = [
    {
        "id": "starter",
        "name": "Starter",
        "price": "RWF 0",
        "price_cents": 0,
        "cadence": "forever",
        "blurb": "One location, manual and spreadsheet import.",
        "features": [
            "1 location",
            "Manual + spreadsheet import",
            "7-day forecast horizon",
            "Email support",
        ],
        "limits": {"locations": 1, "forecasts": 1000, "api_calls": 10000, "history_days": 30},
    },
    {
        "id": "growth",
        "name": "Growth",
        "price": "RWF 48,000",
        "price_cents": 4800000,
        "cadence": "per month",
        "blurb": "Multi-site forecasting with live connectors.",
        "features": [
            "Up to 10 locations",
            "POS + MoMo connectors",
            "30-day horizon with confidence bands",
            "Scheduled reports and webhooks",
        ],
        "limits": {"locations": 10, "forecasts": 10000, "api_calls": 50000, "history_days": 1095},
    },
    {
        "id": "enterprise",
        "name": "Enterprise",
        "price": "Custom",
        "price_cents": None,
        "cadence": "annual",
        "blurb": "Unlimited sites, approvals and data residency.",
        "features": [
            "Unlimited locations",
            "Two-person approvals and audit export",
            "Dedicated model tuning",
            "Data residency and SSO",
        ],
        "limits": {
            "locations": 999,
            "forecasts": 999999,
            "api_calls": 999999,
            "history_days": 9999,
        },
    },
]

PLAN_IDS = {p["id"] for p in PLANS}


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _require_admin(user: User) -> None:
    """Changing the plan, payment method or subscription is the owner's call.

    Read routes (status, plans, usage, invoices) stay open because Billing is a
    personal screen reachable by every role from the user menu.
    """
    if not has_capability(user, BILLING_MANAGE):
        raise _error(403, "forbidden", "Owner role required")


def _get_or_create_subscription(db: Session, workspace_id: uuid.UUID) -> BillingSubscription:
    sub = db.scalar(
        select(BillingSubscription).where(BillingSubscription.workspace_id == workspace_id)
    )
    if sub is None:
        now = datetime.datetime.now(datetime.UTC)
        # Default to growth for demo, starter for new workspaces? Use growth per frontend default
        sub = BillingSubscription(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            plan_id="growth",
            status="active",
            current_period_start=now,
            current_period_end=now + datetime.timedelta(days=30),
            cancel_at_period_end=False,
            created_at=now,
            updated_at=now,
        )
        db.add(sub)
        db.flush()
        # Derive per-plan price from PLANS (not hard-coded 4800000) for future-proofing
        _growth_cents = next((p["price_cents"] for p in PLANS if p["id"] == "growth"), 4800000) or 0
        # Seed 4 invoices for demo (Jun-Sep 2026)
        for idx, (period, amount, status) in enumerate(
            [
                ("Sep 2026", "RWF 48,000", "paid"),
                ("Aug 2026", "RWF 48,000", "paid"),
                ("Jul 2026", "RWF 48,000", "paid"),
                ("Jun 2026", "RWF 0", "trial"),
            ]
        ):
            inv = BillingInvoice(
                id=f"INV-2026-{9-idx:02d}",
                workspace_id=workspace_id,
                subscription_id=sub.id,
                period=period,
                amount_display=amount,
                amount_cents=_growth_cents if "48,000" in amount else 0,
                currency="RWF",
                status=status,
                created_at=now - datetime.timedelta(days=idx * 30),
            )
            # Avoid duplicate PK on reruns
            existing = db.get(BillingInvoice, inv.id)
            if existing is None or existing.workspace_id != workspace_id:
                # Use different id if collision across workspaces
                if existing is not None:
                    inv.id = f"INV-{workspace_id.hex[:4]}-{period.replace(' ','-')}"
                db.add(inv)
        # Seed payment method
        pm = db.scalar(
            select(BillingPaymentMethod).where(BillingPaymentMethod.workspace_id == workspace_id)
        )
        if pm is None:
            pm = BillingPaymentMethod(
                workspace_id=workspace_id,
                brand="visa",
                last4="4242",
                exp_month=9,
                exp_year=2028,
            )
            db.add(pm)
        # Seed contact
        contact = db.scalar(
            select(BillingContact).where(BillingContact.workspace_id == workspace_id)
        )
        if contact is None:
            contact = BillingContact(
                workspace_id=workspace_id, email="finance@demandiq.app", tin="102938475"
            )
            db.add(contact)
        db.flush()
    return sub


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SwitchPlanRequest(BaseModel):
    plan_id: str = Field(description="starter|growth|enterprise")


class UpdatePaymentRequest(BaseModel):
    # Simplified: frontend sends card fields, we store last4/brand
    brand: str = Field(default="visa")
    last4: str = Field(min_length=4, max_length=4, pattern=r"^\d{4}$")
    exp_month: int = Field(ge=1, le=12)
    exp_year: int = Field(ge=2024, le=2040)


class UpdateContactRequest(BaseModel):
    email: EmailStr
    tin: str | None = Field(default=None, max_length=32)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
def billing_status(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Probe — 200 if billing enabled, else 501."""
    # Always enabled in this implementation; frontend gates on 501
    _ = db
    _ = current_user
    return {"enabled": True, "provider": "stripe", "mode": "test", "currency": "RWF"}


@router.get("/plans")
def list_plans(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _ = db
    _ = current_user
    return {"data": PLANS}


@router.get("/subscription")
def get_subscription(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    sub = _get_or_create_subscription(db, current_user.workspace_id)
    plan = next((p for p in PLANS if p["id"] == sub.plan_id), None)
    return {
        "id": str(sub.id),
        "workspace_id": str(sub.workspace_id),
        "plan": plan,
        "plan_id": sub.plan_id,
        "status": sub.status,
        "current_period_start": sub.current_period_start.isoformat(),
        "current_period_end": sub.current_period_end.isoformat(),
        "cancel_at_period_end": sub.cancel_at_period_end,
        "trial_end": sub.trial_end.isoformat() if sub.trial_end else None,
    }


@router.post("/subscription")
def switch_plan(
    payload: SwitchPlanRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    if payload.plan_id not in PLAN_IDS:
        raise _error(400, "invalid_plan", f"Unknown plan '{payload.plan_id}'", field="plan_id")
    _require_admin(current_user)
    sub = _get_or_create_subscription(db, current_user.workspace_id)
    if payload.plan_id == "enterprise":
        # Enterprise is sales-led — create a pending request but don't auto-switch
        return {
            "plan_id": payload.plan_id,
            "status": "pending_sales",
            "message": "Sales will be in touch within one business day.",
        }
    sub.plan_id = payload.plan_id
    sub.status = "active"
    sub.cancel_at_period_end = False
    sub.updated_at = datetime.datetime.now(datetime.UTC)
    db.flush()
    return {"plan_id": sub.plan_id, "status": sub.status, "message": "Plan updated."}


@router.get("/usage")
def get_usage(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    # Compute live usage where possible — never hard-code counts, use 0 fallback for EmptyState
    sub = _get_or_create_subscription(db, current_user.workspace_id)
    plan = next((p for p in PLANS if p["id"] == sub.plan_id), PLANS[1])
    limits = plan["limits"]
    start_month = datetime.datetime.now(datetime.UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    try:
        loc_count = (
            db.scalar(
                select(func.count())
                .select_from(Location)
                .where(
                    Location.workspace_id == current_user.workspace_id,
                    Location.archived_at.is_(None),
                )
            )
            or 0
        )
    except Exception:
        loc_count = 0
    try:
        fc_count = (
            db.scalar(
                select(func.count())
                .select_from(Forecast)
                .where(
                    Forecast.workspace_id == current_user.workspace_id,
                    Forecast.created_at >= start_month,
                )
            )
            or 0
        )
    except Exception:
        fc_count = 0
    # API calls from audit_log where workspace_id and created_at >= start_month
    try:
        from app.models.audit import AuditLog

        api_calls = (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.workspace_id == current_user.workspace_id,
                    AuditLog.created_at >= start_month,
                )
            )
            or 0
        )
    except Exception:
        api_calls = 0
    # Stored history from demand_observations — distinct dates for workspace
    try:
        from app.models.location import DemandObservation

        hist_count = db.scalar(
            select(func.count(func.distinct(DemandObservation.date))).where(
                DemandObservation.workspace_id == current_user.workspace_id
            )
        )
        if hist_count is None:
            hist_count = 0
        # Also consider range fallback (max - min + 1) when distinct is 0 but range exists; keep distinct as primary
        try:
            min_date = db.scalar(
                select(func.min(DemandObservation.date)).where(
                    DemandObservation.workspace_id == current_user.workspace_id
                )
            )
            max_date = db.scalar(
                select(func.max(DemandObservation.date)).where(
                    DemandObservation.workspace_id == current_user.workspace_id
                )
            )
            if min_date is not None and max_date is not None and int(hist_count) == 0:
                hist_count = (max_date - min_date).days + 1
        except Exception:
            # hist_count drives the reported plan usage, so a failure here shows
            # the customer a figure we could not verify — say so in the logs.
            logger.warning(
                "could not derive the observed-history span for usage; reporting the "
                "raw observation count instead",
                exc_info=True,
            )
        hist_used = int(hist_count)
    except Exception:
        hist_used = 0
    usage = [
        {"label": "Locations", "used": int(loc_count), "limit": limits["locations"], "unit": ""},
        {
            "label": "Forecasts this month",
            "used": int(fc_count),
            "limit": limits["forecasts"],
            "unit": "",
        },
        {"label": "API calls", "used": int(api_calls), "limit": limits["api_calls"], "unit": ""},
        {
            "label": "Stored history",
            "used": int(hist_used),
            "limit": limits["history_days"],
            "unit": " days",
        },
    ]
    return {
        "usage": usage,
        "plan": plan,
        "subscription": {"plan_id": sub.plan_id, "status": sub.status},
    }


@router.get("/invoices")
def list_invoices(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _get_or_create_subscription(db, current_user.workspace_id)
    rows = list(
        db.scalars(
            select(BillingInvoice)
            .where(BillingInvoice.workspace_id == current_user.workspace_id)
            .order_by(BillingInvoice.created_at.desc())
        ).all()
    )
    return {
        "data": [
            {
                "id": r.id,
                "period": r.period,
                "amount": r.amount_display,
                "status": r.status,
                "date": r.created_at.strftime("%-d %b %Y") if r.created_at else r.period,
                "pdf_url": r.pdf_url,
            }
            for r in rows
        ]
    }


@router.get("/invoices/{invoice_id}/download")
def download_invoice(
    invoice_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    inv = db.get(BillingInvoice, invoice_id)
    if inv is None or inv.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Invoice not found")
    # In dev, return a placeholder URL
    return {"url": inv.pdf_url or f"https://cdn.demandiq.app/invoices/{inv.id}.pdf", "id": inv.id}


@router.get("/payment-method")
def get_payment_method(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _get_or_create_subscription(db, current_user.workspace_id)
    pm = db.scalar(
        select(BillingPaymentMethod).where(
            BillingPaymentMethod.workspace_id == current_user.workspace_id
        )
    )
    if pm is None:
        raise _error(404, "not_found", "No payment method on file")
    return {
        "brand": pm.brand,
        "last4": pm.last4,
        "exp_month": pm.exp_month,
        "exp_year": pm.exp_year,
        "is_default": pm.is_default,
    }


@router.post("/payment-method")
def update_payment_method(
    payload: UpdatePaymentRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    _get_or_create_subscription(db, current_user.workspace_id)
    pm = db.scalar(
        select(BillingPaymentMethod).where(
            BillingPaymentMethod.workspace_id == current_user.workspace_id
        )
    )
    if pm is None:
        pm = BillingPaymentMethod(
            workspace_id=current_user.workspace_id,
            brand=payload.brand,
            last4=payload.last4,
            exp_month=payload.exp_month,
            exp_year=payload.exp_year,
        )
        db.add(pm)
    else:
        pm.brand = payload.brand
        pm.last4 = payload.last4
        pm.exp_month = payload.exp_month
        pm.exp_year = payload.exp_year
    db.flush()
    return {
        "brand": pm.brand,
        "last4": pm.last4,
        "exp_month": pm.exp_month,
        "exp_year": pm.exp_year,
    }


@router.get("/contact")
def get_contact(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _get_or_create_subscription(db, current_user.workspace_id)
    contact = db.scalar(
        select(BillingContact).where(BillingContact.workspace_id == current_user.workspace_id)
    )
    if contact is None:
        return {"email": "finance@demandiq.app", "tin": "102938475"}
    return {"email": contact.email, "tin": contact.tin}


@router.post("/contact")
def update_contact(
    payload: UpdateContactRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    _get_or_create_subscription(db, current_user.workspace_id)
    contact = db.scalar(
        select(BillingContact).where(BillingContact.workspace_id == current_user.workspace_id)
    )
    if contact is None:
        contact = BillingContact(
            workspace_id=current_user.workspace_id, email=payload.email, tin=payload.tin
        )
        db.add(contact)
    else:
        contact.email = payload.email
        contact.tin = payload.tin
    db.flush()
    return {"email": contact.email, "tin": contact.tin}


@router.post("/cancel")
def cancel_subscription(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    sub = _get_or_create_subscription(db, current_user.workspace_id)
    sub.cancel_at_period_end = True
    sub.status = "active"  # stays active until period end
    sub.updated_at = datetime.datetime.now(datetime.UTC)
    db.flush()
    return {
        "status": "scheduled",
        "message": "Cancellation scheduled for end of period",
        "current_period_end": sub.current_period_end.isoformat(),
    }


@router.post("/resume")
def resume_subscription(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    sub = _get_or_create_subscription(db, current_user.workspace_id)
    sub.cancel_at_period_end = False
    sub.updated_at = datetime.datetime.now(datetime.UTC)
    db.flush()
    return {"status": sub.status, "cancel_at_period_end": False}
