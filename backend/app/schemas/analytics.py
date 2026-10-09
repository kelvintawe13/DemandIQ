"""Analytics schemas — Overview KPIs and `GET /analytics/*` response shapes."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PeriodBounds(BaseModel):
    """Inclusive date bounds for a period."""

    start: datetime.date
    end: datetime.date


class KPICell(BaseModel):
    """Single KPI with previous-period comparison."""

    value: float | int
    previous: float | int | None = Field(default=None)
    change_pct: float | None = Field(default=None, description="Percent change vs previous")
    change_abs: float | int | None = Field(default=None, description="Absolute change")


class SummaryResponse(BaseModel):
    """GET /analytics/summary — KPIs and previous-period deltas."""

    period: PeriodBounds
    previous_period: PeriodBounds = Field(alias="previous_period")

    # Flat KPI fields for direct frontend mapping
    total_visitors: int
    total_visitors_previous: int
    total_visitors_change_pct: float | None = None

    average_daily_demand: float
    average_daily_demand_previous: float
    average_daily_demand_change_pct: float | None = None

    peak_day_visitors: int
    peak_day_date: str | None = None
    peak_day_visitors_previous: int
    peak_day_visitors_change_pct: float | None = None

    growth_rate: float
    growth_rate_delta_pp: float | None = None

    # Nested convenience (optional, not required for tests)
    model_config = {"populate_by_name": True}


class TrendPoint(BaseModel):
    """Single point for trend chart — matches frontend TrendPoint."""

    label: str = Field(description="X-axis label, e.g. '2026-01-10' or 'W01'")
    date: str | None = Field(default=None, description="ISO date for sorting")
    current: int | float
    previous: int | float


class TrendResponse(BaseModel):
    """GET /analytics/trend."""

    granularity: Literal["daily", "weekly"] = "daily"
    period: PeriodBounds
    previous_period: PeriodBounds
    data: list[TrendPoint]


class LocationSlice(BaseModel):
    """Single donut slice — matches frontend LocationSlice."""

    location_id: str
    name: str
    visitors: int
    visitors_formatted: str = Field(description="Locale formatted, e.g. '44,492'")
    share: str = Field(description="Share as percent string, e.g. '34.6%'")
    value: float = Field(description="Share as numeric 0-100")
    color: str = Field(description="Donut color hex")
    rank: int


class ByLocationResponse(BaseModel):
    """GET /analytics/by-location."""

    period: PeriodBounds
    data: list[LocationSlice]
    next_cursor: str | None = None
    has_more: bool = False


class BreakdownRow(BaseModel):
    """Performance breakdown row — matches frontend PerformanceRow + location context."""

    location_id: str | None = None
    location_name: str | None = None
    date: str = Field(description="ISO date or display date")
    visitors: int
    visitors_formatted: str
    previous: int
    previous_formatted: str
    change: str = Field(description="Percent change string, e.g. '17.2%'")
    change_pct: float
    is_positive: bool = Field(alias="isPositive")
    spark: list[int] = Field(description="Sparkline points")

    model_config = {"populate_by_name": True}


class BreakdownResponse(BaseModel):
    """GET /analytics/breakdown — paginated breakdown rows."""

    period: PeriodBounds
    data: list[BreakdownRow]
    next_cursor: str | None = None
    has_more: bool = False


class InsightItem(BaseModel):
    """Single generated insight."""

    type: str = Field(description="peak|drop|weekend|location_share|coverage_miss|trend")
    message: str
    severity: Literal["info", "warning", "critical"] = "info"
    location_id: str | None = None
    date: str | None = None


class InsightsResponse(BaseModel):
    """GET /analytics/insights."""

    period: PeriodBounds
    data: list[InsightItem]
    # Back-compat: also expose list of strings as topInsights
    top_insights: list[str] = Field(default_factory=list, alias="topInsights")

    model_config = {"populate_by_name": True}
