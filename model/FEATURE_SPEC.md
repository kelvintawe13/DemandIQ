# DemandIQ — Feature Specification

Exact definition of every predictor, so the feature job, the model card and
the Feature Store screen in the app all describe the same thing.

> **Scope.** This spec uses the **product schema** (`location_id`, `demand_qty`,
> the app's Feature Store). The offline training pipeline operates on the Recruit
> schema (`air_store_id`, `visitors`, the Japanese holiday calendar); its exact
> 26-column frame and version live in [PIPELINE.md §5](PIPELINE.md), which governs
> the training run. Feature names and the calendar differ between the two schemas;
> the mapping is noted inline below.

Notation: forecasting location `i` for target day `t`. The information
boundary is `as_of = t − 1 @ 23:59` local. **No feature may read a row with
`date >= t`.**

## Historical demand

| Feature | Definition | Null when | Coverage |
| --- | --- | --- | --- |
| `lag_1` | demand at `t−1` | first day of series | 98.2% |
| `lag_7` | demand at `t−7` | under 7 days history | 96.4% |
| `lag_14` | demand at `t−14` | under 14 days history | 94.1% |
| `roll_mean_7` | mean of `t−7 … t−1` | under 7 days | 96.4% |
| `roll_mean_28` | mean of `t−28 … t−1` | under 28 days | 88.7% |
| `roll_std_28` | std dev of `t−28 … t−1` | under 28 days | 88.7% |
| `dow_mean_8w` | mean of the same weekday over the last 8 occurrences | under 8 weeks | 82.3% |
| `dow_std_8w` | std dev of the same | under 8 weeks | 82.3% |
| `trend_7_28` | `roll_mean_7 / roll_mean_28` | either null | 88.7% |

Rolling windows are computed on a **shifted** series. The reference
implementation is one line, and it is the line that must never be edited
carelessly:

```python
df["roll_mean_7"] = (
    df.groupby("location_id")["demand_qty"]
      .transform(lambda s: s.shift(1).rolling(7, min_periods=3).mean())
)
```

Missing history is left null. Boosted trees handle nulls natively; imputing a
zero would teach the model that a new location is a dead location.

*(The offline training frame additionally carries the peak-demand features
`roll_max_7`, `roll_max_28` and `roll_p90_28` — a direct view of the recent tail
— for 12 historical columns in total. See [PIPELINE.md §5.1](PIPELINE.md).)*

## Calendar

| Feature | Type | Notes |
| --- | --- | --- |
| `dow` | categorical 0–6 | Strongest single signal in the set |
| `is_weekend` | boolean | Redundant with `dow` for trees, kept for linear baselines |
| `month` | categorical 1–12 | Seasonality |
| `week_of_year` | int | Long-cycle seasonality |
| `is_holiday` | boolean | Rwandan public holiday calendar |
| `day_before_holiday` | boolean | Demand often shifts forward |
| `day_after_holiday` | boolean | Demand often collapses |
| `is_month_end` | boolean | Payday proximity in the local market |
| `days_since_payday` | int | Approximated as days since the 28th |

All of these are known in advance, so none of them can leak.

*(Naming and scope note: the training frame uses `days_to_month_end` rather than
`days_since_payday`, and its `is_holiday` flags come from the Recruit Japanese
calendar, not the Rwandan one; it also adds `is_weekend_or_holiday`,
`is_weekend_holiday` and `days_to_month_end` for 11 calendar columns. See
[PIPELINE.md §5.2](PIPELINE.md).)*

## Location attributes

| Feature | Type | Notes |
| --- | --- | --- |
| `service_type` | categorical | buffet, canteen, à la carte, café |
| `district` | categorical | Kigali district or pilot region |
| `seating_capacity` | int | Caps plausible demand |
| `open_days_mask` | categorical | Which weekdays the site trades |

Static per location, refreshed only when an administrator edits the registry.

*(Not present in the offline training frame, which has no static location
attributes — see [PIPELINE.md §5](PIPELINE.md).)*

## Reservations — supplementary

| Feature | Definition | Coverage |
| --- | --- | --- |
| `reserved_count` | reservations for `t` **created before `as_of`** | 38.2% |
| `reserve_lead_mean` | mean lead time of those reservations | 38.2% |
| `has_reservation_source` | whether this location has a reservation feed at all | 100% |

Coverage is under 40%, so these are never core predictors. Model variants are
evaluated with and without them, and the production model must not degrade on
locations that have no reservation feed.

*(The offline training frame names these `rsv_visitors`, `rsv_count` and
`rsv_visitors_ratio`; the as-of rule is identical — `reserve_datetime <
visit_date`, strictly before the visit day starts. See [PIPELINE.md §5.3/§5.5](PIPELINE.md).)*

## Excluded, and why

| Candidate | Reason |
| --- | --- |
| `same_day_transactions` | Reads the target day. Direct leakage |
| `weather_actual` | Observed after the fact; only a *forecast* issued before `as_of` is admissible |
| `next_day_reservations` | Future information |
| `monthly_total` | Aggregates across the target day |
| `waste_kg` | Belongs to the independent URBAN-WASTE strand; never joined to demand |

Every exclusion is recorded in the Feature Store screen with this reason
attached, so the decision is visible rather than folklore.

## Versioning

The feature set carries a version string (`fs-2026.09.2`) stored on every
forecast and model version. Changing any definition above is a new version,
even if the column name is unchanged. A model may only serve forecasts built
from the feature-set version it was trained on; a mismatch is a hard error,
not a warning.

## Validation gates

The materialisation job fails — loudly, without writing — if any of these
hold:

1. Any feature window overlaps the target date.
2. A feature's null rate moves more than 10 points from the previous run.
3. A location has a feature row for a date with no corresponding demand row in
   the training set.
4. The feature-set version does not match the manifest.
