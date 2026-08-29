# Phase-2 Results — GBPUSD Value-State Study (2024)

**Status:** Development gate passed
**Branch:** `phase/02-value-state`
**Development interval:** `[2024-01-01, 2025-01-01)`
**Run ID:** `20240101_20250101_2947375b`
**Definitions:** `llm/TECHNICAL_PLAN_GBPUSD_PHASE2.md`

## 1. Decision

Proceed to designing the point-in-time fundamental-bias research layer, while
retaining value/VWAP as context rather than treating either as a standalone
entry signal.

The gate passed because coverage, source-time invariants, group sizes, spread
sanity, and a registered material state contrast all passed. The material
effect is mean reversion at the New York open, not outside-value continuation.
This result does not authorize trading rules, entries, or P&L claims.

## 2. Data and feature quality

- 524 London/New York opening events were generated.
- 520 passed the Phase-1 event-quality rules.
- 518 were value-eligible, or 99.6% of the otherwise eligible events.
- Four events retained their Phase-1 exclusion; two opening events lacked a
  completed previous profile at the start of the source interval.
- 291 FX-day profile rows were observed; 258 passed the 95% M5 coverage rule.
  Short weekend, holiday, and interval-edge rows remain in the audit output but
  cannot be joined as eligible profiles.
- All audited point-in-time invariants passed: ordered VAL/POC/VAH, eligible
  profile coverage, strictly prior profile joins, completed-bar VWAP cutoff,
  unique event IDs, and exact buffered state boundaries.
- Median absolute opening-versus-pre-open spread change was 0.05 pip, below the
  registered 1-pip sanity limit.

VWAP and Volume Profile are based on one unit per observed HistData quote. They
measure quote activity in decentralized spot FX, not centralized traded volume.

## 3. Opening-state sample sizes

| Session | Above value | Inside value | Below value | Total |
|---|---:|---:|---:|---:|
| London | 78 | 116 | 65 | 259 |
| New York | 87 | 95 | 77 | 259 |

Every primary outside/inside group exceeded the frozen minimum of 30 events.

## 4. Registered contrasts

### 4.1 Outside value versus inside value range

There is no clear evidence that opening outside prior value produces a larger
forward range in this development sample.

| Session | Horizon | Outside − inside range | 95% bootstrap CI |
|---|---:|---:|---:|
| London | 30m | +1.02 pip | [-0.97, +2.92] |
| London | 60m | +1.53 pip | [-0.89, +3.90] |
| London | 90m | +1.71 pip | [-1.34, +4.61] |
| New York | 30m | -1.52 pip | [-3.35, +0.20] |
| New York | 60m | +0.03 pip | [-3.07, +3.20] |
| New York | 90m | +0.81 pip | [-2.47, +4.16] |

All intervals include zero.

### 4.2 Outside-value state-aligned return

Positive state-aligned return means continuation away from prior value;
negative means movement back toward it.

| Session | Horizon | Mean | 95% bootstrap CI |
|---|---:|---:|---:|
| London | 30m | +0.54 pip | [-1.08, +2.24] |
| London | 60m | -1.33 pip | [-3.58, +0.88] |
| London | 90m | -2.06 pip | [-4.56, +0.46] |
| New York | 30m | -1.29 pip | [-2.39, -0.20] |
| New York | 60m | **-3.71 pip** | **[-6.23, -1.42]** |
| New York | 90m | **-3.63 pip** | **[-6.30, -1.07]** |

The registered 2-pip materiality gate is met at New York 60 and 90 minutes.
The sign rejects a naive outside-value breakout-continuation assumption and
instead supports testing a value-reversion interaction with fundamental bias.
London does not show a statistically resolved outside-value directional effect.

## 5. Transition and VWAP observations

Within 60 minutes, 20%–24% of outside-value openings recorded a completed M5
close back across the raw nearest value boundary. The acceptance label is an
"ever observed" two-close event, so an opening can accept outside early and
still re-enter later; acceptance and re-entry are not mutually exclusive
terminal states.

Exploratory Spearman associations between VWAP distance/z-score/slope and
forward return/range are modest. The largest absolute association in the
registered horizons is about 0.18. This is not sufficient to claim a standalone
linear or monotonic VWAP edge, but the event-level continuous features and
sufficiently populated joint VWAP/value cells are retained for the next
conditional study.

## 6. Interpretation limits

- This is one development year, not out-of-sample validation.
- The activity proxy can be feed-dependent and does not equal broker or
  exchange volume.
- Bootstrap intervals quantify sampling uncertainty in the observed events but
  do not correct for all regime, dependence, or multiple-comparison risks.
- The result contains no spread-paid entries, slippage, stops, exits, position
  sizing, or P&L simulation.
- The negative state-aligned New York result says average price moved toward
  value; it does not by itself specify a tradable trigger or achievable fill.

## 7. Reproducibility

Generated artifacts are local and ignored by Git:

```text
data/processed/reports/phase2/20240101_20250101_2947375b/
```

The run manifest records the full project/value configuration, hashes for all
12 HistData source archives, runtime versions, row counts, Git base commit, and
the working-tree state, and the complete development-gate decision. Re-run with:

```bash
.venv/bin/python -m gbpusd_research build-range \
  --research config/research_2024.yaml --force
.venv/bin/python -m gbpusd_research run-phase2 \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml
```

## 8. Next research question

Before constructing technical entries, define a point-in-time GBP-minus-USD
fundamental score and test whether it separates the New York value-reversion
effect or identifies the subset that instead continues outside value. The
fundamental definition and timestamps must be frozen before observing its
conditional results.
