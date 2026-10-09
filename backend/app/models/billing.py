"""Billing models — plans are config, subscription/invoices/payment persisted."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BillingSubscription(Base):
    """Workspace subscription — one active per workspace."""

    __tablename__ = "billing_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    plan_id: Mapped[str] = mapped_column(Text, nullable=False)  # starter|growth|enterprise
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active"
    )  # active|trialing|cancelled|past_due
    current_period_start: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    current_period_end: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trial_end: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class BillingInvoice(Base):
    """Invoice per billing period."""

    __tablename__ = "billing_invoices"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # INV-2026-09
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("billing_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    period: Mapped[str] = mapped_column(Text, nullable=False)  # Sep 2026
    period_start: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount_display: Mapped[str] = mapped_column(Text, nullable=False, default="RWF 0")
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="RWF")
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="paid"
    )  # paid|trial|open|void|cancelled
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class BillingPaymentMethod(Base):
    """Card / MoMo on file — encrypted at rest in prod, plain here for demo."""

    __tablename__ = "billing_payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    brand: Mapped[str] = mapped_column(Text, nullable=False, default="visa")  # visa|mastercard|momo
    last4: Mapped[str] = mapped_column(Text, nullable=False, default="4242")
    exp_month: Mapped[int] = mapped_column(Integer, nullable=False, default=9)
    exp_year: Mapped[int] = mapped_column(Integer, nullable=False, default=2028)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class BillingContact(Base):
    """Where invoices are sent."""

    __tablename__ = "billing_contacts"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    email: Mapped[str] = mapped_column(Text, nullable=False, default="finance@demandiq.app")
    tin: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
