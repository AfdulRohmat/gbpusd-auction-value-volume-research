# Phase-3 Results — GBPUSD Fundamental Policy Bias (2024)

**Status:** Development gate failed
**Branch:** `phase/03-fundamental-bias`
**Development interval:** `[2024-01-01, 2025-01-01)`
**Run ID:** `20240101_20250101_4e391fe0`
**Definitions:** `llm/TECHNICAL_PLAN_GBPUSD_PHASE3.md`

## 1. Decision

Do not proceed directly to technical-entry construction on the evidence from
`policy_bias_v1` alone.

The Phase-3 pipeline, point-in-time joins, coverage, group sizes, and regime
breadth all passed. The research gate failed only because no registered
fundamental contrast reached the frozen 2-pip materiality threshold with a 95%
bootstrap interval excluding zero.

This result must not be repaired by changing the 90-day lookback, score weights,
or thresholds after observing 2024.

## 2. Frozen V1 model

The score used only official policy decisions known at each event:

```text
carry_signal = sign(GBP Bank Rate - USD target-range midpoint)
impulse_signal = sign(GBP 90d rate change - USD 90d midpoint change)
relative_score = carry_signal + impulse_signal
bias = sign(relative_score)
```

The seven-event source ledger includes one pre-period anchor and every 2024
rate change for each central bank. Bank of England decisions are available at
12:00 London time; Fed decisions at 14:00 New York time. No inflation, labor,
GDP, consensus, or revised current-history series entered the score.

## 3. Data quality

- 524 opening events were retained from the Phase-2 artifact.
- 518 events were Phase-2 eligible and all 518 received valid fundamental
  features: 100% incremental coverage.
- Six Phase-2 exclusions remain excluded; there are no additional missing-policy
  exclusions.
- All point-in-time checks passed: current and lookback timestamps, score
  arithmetic, sign mapping, source ordering, and event-ID uniqueness.
- Short bias spans nine calendar months; long bias spans four.
- The single neutral observation is the New York event on 7 November, after the
  BoE decision but before the Fed decision later that day.

## 4. Bias counts

| Session | Long | Neutral | Short | Eligible |
|---|---:|---:|---:|---:|
| London | 73 | 0 | 186 | 259 |
| New York | 72 | 1 | 186 | 259 |

This asymmetry reflects the enacted policy-rate regimes during 2024; it is not
a fitted class balance.

## 5. Registered directional contrast

Positive bias-aligned return means GBPUSD moved in the direction selected by
the policy score.

| Session | Horizon | Mean bias-aligned return | 95% bootstrap CI |
|---|---:|---:|---:|
| London | 15m | -0.32 pip | [-1.43, +0.75] |
| London | 30m | +0.09 pip | [-1.20, +1.30] |
| London | 60m | +0.42 pip | [-1.26, +2.06] |
| London | 90m | -0.59 pip | [-2.53, +1.33] |
| New York | 15m | -0.01 pip | [-0.73, +0.72] |
| New York | 30m | +0.22 pip | [-0.77, +1.21] |
| New York | 60m | +1.32 pip | [-0.55, +3.23] |
| New York | 90m | +0.37 pip | [-1.63, +2.38] |

Every interval includes zero and every mean is below the registered 2-pip
materiality threshold. The policy score therefore does not demonstrate a
standalone session-direction edge in this sample.

## 6. Interaction with outside-value reversion

For openings outside prior value, positive reversion-aligned return means price
moved toward value. The comparison below is:

```text
policy supports value reversion - policy opposes value reversion
```

| Session | Horizon | Difference | 95% bootstrap CI |
|---|---:|---:|---:|
| London | 30m | +2.22 pip | [-1.02, +5.55] |
| London | 60m | +1.74 pip | [-2.74, +6.11] |
| London | 90m | -1.12 pip | [-6.21, +4.06] |
| New York | 30m | +0.90 pip | [-1.31, +3.15] |
| New York | 60m | +4.45 pip | [-0.16, +9.39] |
| New York | 90m | +2.21 pip | [-2.87, +7.53] |

New York at 60 minutes is suggestive but does not pass: its interval still
crosses zero. It is also a one-year regime comparison and the support/opposition
groups contain different mixes of above-VAH and below-VAL events. It must not be
promoted to a trading rule.

## 7. Interpretation limits

- This is a deliberately narrow monetary-policy score, not a complete
  fundamental model.
- Enacted rate levels are slow-moving and may already be priced before the
  announcement; the model contains no expectations or surprise component.
- Long and short labels are concentrated in different parts of 2024, so score
  effects are inseparable from time regime in a single development year.
- The sample contains no out-of-year validation.
- No entry, spread-paid execution, slippage, stop, exit, sizing, or P&L has been
  simulated.

## 8. Reproducibility

Generated artifacts are local and ignored by Git:

```text
data/processed/reports/phase3/20240101_20250101_4e391fe0/
```

The manifest records the Phase-2 input manifest hash, complete configuration,
policy-ledger hash, Git state, runtime, row counts, and gate decision. Re-run:

```bash
.venv/bin/python -m gbpusd_research run-phase3 \
  --research config/research_2024.yaml \
  --value-state config/value_state.yaml \
  --fundamental config/fundamental_bias.yaml
```

## 9. Recommended next decision

Keep the Phase-3 implementation as the monetary-policy baseline, but pause
Phase 4. The next defensible options are:

1. validate the frozen score on another clean market-data year; or
2. preregister a separate Phase-3 extension using genuinely release-timestamped,
   revision-safe inflation/expectations data before seeing its outcomes.

Do not silently replace the failed V1 result or tune its rules on 2024.
