# DemandIQ — Evaluation Protocol

How a model is judged, and what must be true before it is allowed to serve a
forecast anyone acts on. This is the document to point at when someone asks
"how do you know it works".

## The question being evaluated

Not "does the model fit the data" but **"does following this model's
recommendation leave the kitchen better off than what they do today?"** That
reframing decides every choice below: the baselines are what operators
actually do, and the headline metric is a decision cost, not a fit statistic.

## Splitting

Time-ordered only. A random split leaks the future into training and inflates
every number.

- **Train:** everything up to `T − 90` days.
- **Validation:** `T − 90` to `T − 30`, for hyperparameters and early stopping.
- **Test:** the final 30 days, touched once, at the end.
- **Rolling origin:** 6 folds, each advancing the origin by 14 days, so the
  reported figure is a mean over folds rather than a lucky window.

Locations with under 60 days of history are excluded from evaluation and
served by the historical-average fallback. Including them flatters the model:
their variance is low and their level is easy.

> **Implementation.** The above is the general protocol. The pipeline
> parameterises the boundary as a single `--test-start` and takes validation as
> the tail of the training window; [PIPELINE.md §6](PIPELINE.md) states exactly
> what runs.

## Baselines

A model that cannot beat these has no right to production:

| Baseline | Definition |
| --- | --- |
| Naive | yesterday's demand |
| Seasonal naive | the same weekday last week |
| Historical average | mean of the last 28 comparable days |
| Weekday mean | mean of the same weekday over 8 weeks |

The historical average is the real bar — it is approximately what an
experienced manager does in their head.

## Metrics

**Point accuracy**

| Metric | Why it is here |
| --- | --- |
| MAE | Primary. Same units as covers, robust to the occasional wild day |
| RMSE | Secondary. Surfaces large misses MAE can hide |
| MASE | Scale-free, so locations of different sizes can be compared |
| Bias (ME) | A model that is right on average but always low is not fine |

MAPE is deliberately **not** used: it explodes on low-demand days and would
systematically misrepresent small sites.

**Interval quality**

| Metric | Target |
| --- | --- |
| Empirical coverage | Within ±2 points of the nominal 90% |
| Mean interval width | As narrow as possible at that coverage |
| Winkler score | Penalises wide intervals and missed observations together |

Coverage alone is gameable — an interval of 0 to infinity has perfect
coverage. Width is always reported beside it. (The Winkler score is part of this
intended protocol; the pipeline currently computes coverage, mean width and the
per-stratum breakdown — see [PIPELINE.md §8.6](PIPELINE.md).)

**Decision quality** — the metrics that actually matter:

| Metric | Definition |
| --- | --- |
| Simulated surplus (PS) | `Σ max(0, prep − actual)` |
| Unmet demand (UD) | `Σ max(0, actual − prep)` |
| Days short | Count of days where `actual > prep` |
| Policy frontier | PS against UD across quantiles 0.5 to 0.95 |

These are visitor-equivalents, never kilograms. The frontier is reported
rather than a single point, because the right operating quantile is a business
judgement, not a modelling result.

## Slices

An aggregate number hides the failures that lose trust. Every evaluation
reports the same metrics broken down by:

- location, and separately by service type;
- day of week, with weekends called out;
- holidays and their eves;
- the first 30 days of a location's life;
- the highest and lowest demand deciles.

A model may not be promoted if it degrades a slice by more than 15% against
production, even when the aggregate improves.

### Feature-set sensitivity: Golden Week / holiday-run candidates (2026-09-20)

A candidate feature set `fs-2026.09.3` (Golden Week window + signed distance,
non-workday run depth, days-to-next-holiday) was built, tested and **not
promoted**. It improved the validation tail (MAE 6.454 vs 6.466) but degraded
the held-out window (MAE 6.713 vs 6.702; tail ≥60 visitors: 28.67 vs 28.51).
Root cause: the held-out window (2017-02-01 → 2017-04-22) contains no Golden
Week days, so the features carry signal only where they cannot be validated
here. The implementation is documented for the deployment window (post
2017-04-22, which contains actual Golden Week — the original competition's
final test weeks) and should be re-evaluated on data that includes one.

## Promotion gates

All five must pass:

1. MAE improves over production by at least 3% on the rolling-origin mean.
2. No slice degrades by more than 15%.
3. Empirical coverage lands within 88–92%.
4. Inference stays under 300 ms per location-day at p95.
5. A model card exists, naming the training window, feature-set version,
   hyperparameters, metrics by slice, and known weaknesses.

Gates are evaluated automatically; the promotion itself still requires a
second human approval.

## Post-release monitoring

| Signal | Action |
| --- | --- |
| Rolling 14-day MAE worsens 20% | Alert, investigate |
| Coverage outside 85–95% for 7 days | Recalibrate conformal step size |
| PSI over 0.2 on any core feature | Flag drift, schedule retrain |
| Fallback rate above 10% | Data problem, not a model problem |

Recalibration is always tried before retraining. Most coverage drift is an
interval problem, not a point-forecast problem, and retraining first hides
that.

## Honesty constraints on reporting

- Never quote an accuracy figure without its evaluation window and slice.
- Never compare against a baseline the model was tuned on.
- Never report surplus reduction as waste reduction.
- Publish the failure modes in the model card: this model is weakest on
  holidays, on locations under 90 days old, and on the top demand decile.
