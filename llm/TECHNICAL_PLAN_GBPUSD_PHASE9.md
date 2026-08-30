# Technical Plan — GBPUSD Exness Quote-Activity Phase 9

**Status:** Frozen before receiving or inspecting Exness data
**Branch:** `phase/09-exness-quote-activity-final`
**Instrument/account:** GBPUSD, Exness Raw Spread, MetaTrader 5
**Authority:** final research iteration; no live-trading authorization

## 1. Decision this phase must answer

Phase 9 tests one narrow claim:

> Does quote-arrival activity from the broker feed add stable, tradable information
> beyond price alone at the London and New York opens?

This is not a test of centralized traded volume. Spot-FX/CFD tick history contains
Bid/Ask quote updates. The terms `quote activity`, `tick count`, and `directional
quote imbalance` must be used throughout the implementation and report. They must
not be relabeled as traded volume, order flow, buy volume, or sell volume.

Phase 9 is the final iteration in this repository. If the frozen success gate
fails, the repository will be closed with a consolidated negative-result
conclusion in `README.md`; no new strategy variant will be introduced.

## 2. Data hierarchy

Preferred evidence is an export obtained with `CopyTicksRange` while MetaTrader 5
is connected to the user's Exness Raw Spread account. It most closely represents
the server and symbol used for execution.

The fallback is the Exness Personal Area Tick History archive. Exness documents
its Raw Spread archive as coming from an MT4 Real server and warns that ticks can
differ slightly across servers because of latency. A fallback result must
therefore be labeled `Exness Raw Spread reference feed`, not the exact MT5 account
feed.

Required columns are:

```text
source, symbol, timestamp/time_msc, bid, ask
```

Optional MT5 fields (`flags`, `last`, `volume`, and `volume_real`) are retained
for audit but may not be used as traded-volume evidence unless they are non-zero,
documented for GBPUSD by the broker, and pass a separately registered review.
That review is not part of Phase 9.

All timestamps are normalized to UTC. Accepted symbols are `GBPUSD` and
`GBPUSD-r`. Duplicate timestamp/Bid/Ask rows are removed without aggregating
distinct quotes that share a millisecond.

## 3. Frozen evidence periods

| Role | Half-open interval | Use |
|---|---|---|
| Development | `[2024-01-01, 2025-01-01)` | fit scaler and model coefficients once |
| Replication | `[2025-01-01, 2026-01-01)` | no fitting or threshold changes |
| Forward holdout | `[2026-01-01, 2026-08-01)` | final untouched decision period |

The forward endpoint is the last complete month available when this plan was
frozen. If forward data cannot be obtained, Phase 9 can produce only a
preliminary result and cannot support the final repository verdict.

## 4. Event and observability contract

- Events are every registered weekday London and New York open.
- Observation window: `[open, open + 15 minutes)`.
- Entry time: first quote at or after `open + 15 minutes`.
- Activity baseline: `[open - 60 minutes, open)`.
- London management cutoff: registered New York open on the same civil date.
- New York management cutoff: registered 17:00 New York FX-day boundary.
- Features use only quotes strictly before the entry time.
- An event requires at least one tick in every observation M5 bucket, a complete
  pre-open activity baseline, an entry quote, and quotes through its cutoff.
- There is at most one directional decision per eligible session, targeting the
  user's approximately forty monthly London/New York opportunities.

## 5. Registered predictors

### 5.1 Price-only baseline

Computed from the first three completed M5 bars:

```text
opening_return_pips
opening_range_pips
opening_efficiency = abs(opening return) / sum(abs(M5 close changes))
opening_close_location = (close - low) / (high - low)
```

### 5.2 Quote-activity additions

```text
log_activity_ratio = log(mean opening tick count / mean pre-open tick count)
directional_quote_imbalance = (up updates - down updates) / non-flat updates
activity_acceleration = last opening M5 count / mean(first two opening counts)
spread_ratio = opening median spread / pre-open median spread
```

An up or down update is the sign of the change in midpoint from the immediately
previous quote. It is not an aggressor-side classification.

### 5.3 Frozen model variants

```text
price_only       = price predictors
activity_only    = quote-activity predictors
price_activity   = both predictor sets
```

Each is a deterministic L2-regularized logistic regression. Standardization,
missing-value medians, and coefficients are fit on 2024 only. The L2 coefficient
is fixed at `1.0`; no grid search, feature selection, probability threshold
tuning, or year-specific refit is allowed. Probability `>= 0.50` selects long;
otherwise it selects short.

The binary label is the sign of the executable midpoint change from entry to the
registered session cutoff. Zero-return labels are excluded before fitting.

## 6. Execution variants

All three models use identical execution so that the incremental activity value
is isolated.

- Long entry: entry Ask plus `0.1` pip adverse slippage.
- Short entry: entry Bid minus `0.1` pip adverse slippage.
- Initial long stop: opening low minus `1.0` pip.
- Initial short stop: opening high plus `1.0` pip.
- Eligible initial risk: `5` through `30` pips.
- Long exits are triggered/finalized on Bid; short exits on Ask.
- Same-tick or same-bar ambiguity is resolved stop-first.
- Stop exits include adverse gap handling and `0.1` pip slippage.
- Commission is frozen conservatively at USD `3.50` per standard lot per side.
  For GBPUSD this is modeled as `0.35` pip per side using USD `10` per pip per
  standard lot.

Registered exits:

1. `fixed_2r`: initial stop or fixed `2R` target, otherwise cutoff exit.
2. `trailing_session`: initial stop; after `+1R`, stop moves to break-even and
   then follows the prior three completed M5-bar swing with a one-pip buffer;
   otherwise cutoff exit.

No position sizing, compounding, overlapping-portfolio leverage, or discretionary
skip is introduced.

## 7. Predictive evaluation

Report for each period, model, and session:

- eligible events and exclusions;
- class balance and long/short decisions;
- accuracy, balanced accuracy, ROC AUC, log loss, and Brier score;
- probability calibration by quintile; and
- paired `price_activity - price_only` deltas.

The primary predictive gate must pass independently in 2025 and 2026:

```text
price_activity AUC >= 0.53
AUC improvement over price_only >= 0.01
log-loss improvement over price_only >= 0.005
```

Failure of either out-of-sample period means quote activity has not demonstrated
stable incremental predictive value.

## 8. Trading evaluation

Report by period, model, exit, and session:

- trades and trades per calendar month;
- win rate, average winner/loser, payoff ratio;
- net expectancy R, net R, profit factor, and maximum drawdown;
- month-cluster 95% confidence interval for mean R;
- MFE, MAE, holding time, and exit attribution; and
- paired expectancy delta against `price_only`.

The primary trading gate is the `price_activity / fixed_2r` result. It must pass
independently in 2025 and 2026:

```text
eligible trades >= 20 per calendar month on average
net expectancy > 0R
profit factor > 1.05
expectancy improvement over price_only >= 0.05R
month-cluster 95% interval lower bound > 0R
```

The trailing result is a registered secondary analysis and cannot rescue a
failed primary result.

## 9. Final decision rule

`candidate_edge` requires every predictive and primary trading condition to
pass in both 2025 and the 2026 forward holdout. Anything else is
`no_robust_edge`.

For `no_robust_edge`, the work ends with:

1. a Phase-9 results document;
2. a README synthesis covering Phases 1–9 and the limits of decentralized
   quote activity;
3. a reproducibility command and artifact manifest;
4. merge to `main` while preserving every phase branch; and
5. no additional hypothesis or threshold iteration in this repository.

For `candidate_edge`, the result remains a research candidate, not permission
to trade live. A paper-trading stage outside this repository would still be
required.

## 10. Leakage and integrity checks

- No 2025/2026 values influence imputation, scaling, coefficients, or decisions.
- Feature timestamps must precede entry timestamps.
- Outcomes and exit ticks must be after entry.
- London/New York calendars use IANA civil-time rules and DST conversion.
- Bid/Ask execution and commission are applied to every trade.
- Re-running the importer over identical archives produces identical hashes and
  M5 results.
- Raw archives, extracted CSVs, model artifacts, and generated reports remain
  gitignored; only code, configuration, manifests without raw content, and
  written conclusions are committed.
