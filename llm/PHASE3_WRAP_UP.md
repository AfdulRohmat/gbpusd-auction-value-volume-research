# Phase 3 Wrap-Up — GBPUSD Fundamental Bias Research

**Status:** Complete; all registered development gates failed  
**Branch:** `phase/03-fundamental-bias`  
**Development interval:** `[2024-01-01, 2025-01-01)`  
**Next research branch:** `phase/04-volume-profile-validation`

## 1. Final decision

Close the Phase-3 directional-fundamental research line without promoting a
fundamental signal into entry or P&L construction.

This is a negative hypothesis result, not a pipeline or data-quality failure.
All three registered models were implemented reproducibly and evaluated under
point-in-time constraints, but none demonstrated a stable directional edge in
the 2024 development sample.

## 2. Models tested

### Phase 3 V1 — policy-only bias

The first model combined relative BoE/Fed policy carry and recent rate-change
impulse. Its development gate failed because no registered directional or
Volume Profile interaction contrast was both material and statistically
resolved.

Result: `llm/PHASE3_RESULTS_2024.md`.

### Phase 3B — four-pillar relative strength

The second model independently scored GBP and USD policy, inflation, earnings,
and two-year yield momentum with equal primary weights. A `3-2-2-1` impact
weighting was retained as sensitivity only.

The gate failed because the long class was sparse and every registered primary
confidence interval included zero. Impact weighting did not improve the result.

Result: `llm/PHASE3B_RESULTS_2024.md`.

### Phase 3C — market-implied surprise proxy

Historical actual-minus-consensus data could not be sourced cleanly under the
zero-cost and reproducibility constraints. The separately registered fallback
used event-day two-year yield repricing after 64 official policy, CPI, and
earnings catalyst bundles.

All 64 catalysts mapped successfully and all 518 Phase-2 eligible opens received
a point-in-time state. The primary three-session-day means were +2.59 pips for
London and +4.53 pips for New York, but both were below the +6-pip materiality
rule and their 97.5% cluster-bootstrap intervals crossed zero. One-day effects
were negative, so horizon consistency also failed.

Result: `llm/PHASE3C_RESULTS_2024.md`.

## 3. Consolidated conclusion

The evidence supports only this bounded statement:

> In GBPUSD during the 2024 development interval, the registered policy state,
> relative four-pillar strength, and post-release two-year yield repricing
> models did not produce a stable London or New York directional bias.

It does not prove that fundamental information can never affect GBPUSD. It does
show that these exact zero-cost formulations should not be tuned or promoted
using the same 2024 outcomes.

Exploratory subgroups—especially the Phase-3C labor split—are not authorized as
new models because they were observed after opening the development result and
have too few independent regimes. Testing them would require a new frozen plan
and untouched data.

## 4. What Phase 3 delivered

- validated official BoE, Federal Reserve, ONS, BLS, Bank of England yield, and
  US Treasury reference ledgers;
- explicit publication latency and point-in-time feature contracts;
- session-specific bias attachment that permits London and New York to differ;
- equal-weight and impact-weight sensitivity implementations;
- event-day market-repricing and same-session 1/3/5-day outcome tooling;
- deterministic bootstrap inference, manifests, source hashes, reports, and
  regression tests; and
- preserved negative results that prevent future repetition and data-mined
  parameter selection.

## 5. Handoff to Phase 4

Phase 2 remains the strongest surviving research direction: outside previous
value showed a candidate reversion structure, while VWAP did not demonstrate an
independent directional edge.

Phase 4 must start on its own branch and freeze the Volume Profile setup before
opening validation outcomes. The intended research boundary is:

1. use 2024 only to define the candidate outside-value setup;
2. include executable entry, stop, target, timeout, spread, and slippage rules;
3. use 2025 as an untouched validation interval;
4. separate London and New York results and preserve all exclusions;
5. reject, rather than retune, the setup if the frozen validation gate fails.

Fundamental information may later be studied as a news-risk exclusion filter,
but it is not carried into Phase 4 as a directional requirement.
