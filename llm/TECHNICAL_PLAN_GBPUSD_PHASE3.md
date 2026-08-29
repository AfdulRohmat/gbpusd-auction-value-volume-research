# Technical Plan — GBPUSD Fundamental Bias Phase 3

**Status:** Approved for implementation on `phase/03-fundamental-bias`
**Source PRD:** `llm/PRD_GBPUSD_Session_Value_Fundamental_Research.md`
**Development sample:** `[2024-01-01, 2025-01-01)`
**Phase-2 dependency:** `llm/PHASE2_RESULTS_2024.md`

## 1. Objective and boundary

Phase 3 tests whether a minimal, point-in-time GBP-minus-USD monetary-policy
bias explains session-opening direction and the New York outside-value
mean-reversion effect found in Phase 2.

This remains a conditional event study. It does not define M5 entries, simulate
fills, apply stops or targets, calculate P&L, or optimize parameters. A positive
result only authorizes technical-setup construction in the next phase.

## 2. Frozen V1 data scope

The primary model is named `policy_bias_v1`. It uses only enacted policy-rate
decisions from official central-bank publications:

- Bank of England Bank Rate decisions; and
- Federal Reserve target federal funds range decisions.

The checked-in event ledger stores the exact public availability timestamp,
rate/range, and official source URL. Bank of England decisions become available
at 12:00 local London time. Federal Reserve decisions become available at 14:00
New York time. UTC conversions use the civil-time offset applying on each
announcement date.

Only rate changes needed to reconstruct the 2024 state plus one pre-period
anchor per currency are included. Policy decisions are treated as immutable
official events. The ledger is hashed in every run manifest.

Inflation, labor, GDP, and survey data are excluded from the primary V1 score.
Free current-history downloads can contain later revisions, while reliable
vintage/release-timestamp histories are not yet available in this repository.
Adding those values now would weaken rather than improve point-in-time safety.

## 3. Frozen score

At event timestamp `t`, select the latest GBP and USD policy observations with
`available_at_utc <= t`. The US rate is the midpoint of its target range.

Two unweighted relative components are calculated:

```text
carry_spread(t) = GBP_rate(t) - USD_midpoint(t)
carry_signal(t) = sign(carry_spread(t))

GBP_impulse(t) = GBP_rate(t) - GBP_rate(t - 90 days)
USD_impulse(t) = USD_midpoint(t) - USD_midpoint(t - 90 days)
impulse_spread(t) = GBP_impulse(t) - USD_impulse(t)
impulse_signal(t) = sign(impulse_spread(t))

relative_score(t) = carry_signal(t) + impulse_signal(t)
```

There is no fitted threshold. Exact zero maps to zero. Output bias is:

- `+1 / long` when `relative_score > 0`;
- `0 / neutral` when `relative_score == 0`; and
- `-1 / short` when `relative_score < 0`.

The 90-day impulse window is a fixed calendar-quarter proxy. No alternative
lookbacks will be selected using 2024 outcomes.

Every event stores both current source timestamps, current rates, lookback
rates, rate changes, component signals, relative score, and final bias.

## 4. Point-in-time rules

- A policy observation at the same timestamp as an event is available.
- A later observation must not affect that event under any circumstance.
- London events before a noon BoE decision retain the previous Bank Rate.
- A same-day New York event after a noon BoE decision may use the new rate.
- Fed decisions occur after the configured New York open and therefore first
  affect later events.
- Both current and 90-day lookback observations must exist; otherwise the event
  is explicitly excluded from fundamental analysis.

## 5. Registered analyses

For London and New York separately at 15/30/60/90 minutes:

1. estimate `bias * forward_return` for non-neutral bias events versus zero;
2. report return, range, MFE, and MAE by long/neutral/short bias;
3. for outside-value openings, define value-reversion direction as short above
   VAH and long below VAL;
4. compare reversion-aligned return when policy bias supports versus opposes
   that direction; and
5. display joint value-state/bias cells only when they contain at least 30
   observations.

Bootstrap confidence intervals use the existing 10,000 resamples, 95%
confidence level, and fixed seed. Secondary breakdowns are exploratory.

## 6. Development gate

Phase 3 supports technical-setup construction only when:

- fundamental features cover at least 95% of Phase-2 eligible events;
- all source and availability invariants pass;
- every accepted primary comparison satisfies the frozen 30-event minimum;
- each directional bias used by the gate spans at least two calendar months;
- at least one registered contrast is at least 2 pips with its 95% bootstrap
  interval excluding zero.

A failed gate means this minimal policy-rate bias has not shown enough
incremental information. It does not justify tuning the score on 2024 or adding
revised macro series after observing the outcome.

## 7. Implementation layout

```text
config/fundamental_bias.yaml
data/reference/policy_rate_events.csv
src/gbpusd_research/
├── data/macro.py
├── features/fundamentals.py
└── research/
    ├── fundamental_bias.py
    └── phase3.py
tests/
├── test_macro.py
├── test_fundamentals.py
├── test_fundamental_bias.py
└── test_phase3.py
```

Command:

```bash
python -m gbpusd_research run-phase3 \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental config/fundamental_bias.yaml
```

## 8. Report contract

```text
data/processed/reports/phase3/<run_id>/
├── run_manifest.json
├── data_quality.json
├── policy_timeline.csv
├── fundamental_events.parquet
├── event_exclusions.parquet
├── conditional_statistics.csv
├── statistical_comparisons.csv
├── figures/
│   ├── bias_counts.png
│   ├── aligned_return_by_horizon.png
│   ├── value_reversion_interaction.png
│   └── policy_timeline.png
└── report.md
```

Generated reports remain ignored by Git. The manifest records Phase-2 input
provenance, configuration snapshots/hashes, the policy-ledger hash, Git state,
runtime versions, row counts, and the gate decision.

## 9. Required tests

- strict schema, currency, ordering, uniqueness, and rate-range validation;
- exact UTC decision availability across London/New York DST;
- 90-day as-of lookup and component arithmetic;
- exact-zero neutral mapping;
- future sentinel policy events cannot alter an earlier score;
- bias-aligned and value-reversion-aligned outcome direction;
- deterministic bootstrap output;
- group-size and month-breadth gates; and
- synthetic orchestration produces every report artifact.

## 10. Delivery sequence

1. validate and hash the official policy-event ledger;
2. attach point-in-time policy states and scores to Phase-2 events;
3. calculate directional and value-reversion interactions;
4. generate the complete Phase-3 report;
5. review the development gate before any technical entry code.
