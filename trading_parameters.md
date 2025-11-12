Good question. Let’s stop hand-waving and put numbers on this thing.

Below is a pragmatic **v1 parameter set** for your architecture that’s:

* conservative,
* realistic for a serious retail/serious-hobbyist stack,
* easy to tune later.

Assume:

* Spot only, on Coinbase.
* Account size: **$10K–$100K** (scales fine).
* Medium frequency (minutes–hours), not HFT, not weekly-only.

You can drop this straight into `policy.yaml` / `app.yaml` style configs.

---

## 1. Universe & Liquidity

```yaml
universe:
  min_24h_volume_usd: 20_000_000       # below this = not worth it
  max_spread_bps: 60                   # 0.60% max at the top of book
  min_depth_20bps_usd: 50_000          # depth within 0.2% of mid
  include_tiers:
    tier1_majors: [BTC, ETH, SOL]
    tier2_liquid_alts: true            # auto-selected via filters
    tier3_event_driven: true           # only via news/event triggers
  blacklist:
    - any_leveraged_tokens
    - obvious_meme_trash_you_dont_trust
```

Why:

* $20M+ / day and ≤60 bps spread keeps fills sane.
* Tier1 pinned so you always consider majors.
* Tier3 only unlocked by real catalysts (ETF, listing, upgrades, etc).

---

## 2. Global Risk & Exposure

Core rule: **survive first, optimize later.**

```yaml
risk:
  max_total_at_risk_pct: 0.15          # 15% of NLV in "active risk" positions
  max_per_asset_pct: 0.05              # 5% of NLV per coin
  max_per_theme_pct:
    L2: 0.10                           # 10% across ARB/OP/STRK/etc
    MEME: 0.05
    DEFI: 0.10
  max_trades_per_day: 10
  max_new_trades_per_hour: 4

  daily_stop_pnl_pct: -0.03            # -3% NLV on the day → stop new trades
  weekly_stop_pnl_pct: -0.07           # -7% week → tighten / only de-risk

  cooldown:
    after_loss_trades: 3               # 3 losing trades in a row → pause new entries
    cooldown_minutes: 60

  min_trade_notional_usd: 100          # skip dust
```

This keeps you from YOLO-ing concentration or bleeding slowly to death.

---

## 3. Triggers (When to Even Consider a Trade)

These run on math only.

```yaml
triggers:
  lookback_minutes_short: 15
  lookback_minutes_medium: 60

  price_move:
    high_severity_pct_15m: 4.0         # >=4% in 15m
    medium_severity_pct_60m: 6.0       # >=6% in 60m

  volume_spike:
    ratio_1h_vs_24h: 2.0               # 2x+ hourly vs 24h avg → interesting

  breakout:
    new_high_lookback_hours: 24        # 24h high with volume confirmation
    new_low_lookback_hours: 24

  min_liquidity_for_trigger:
    vol_24h_usd: 20_000_000
    max_spread_bps: 60

  severity_buckets:
    high: any(high_price_move or big_volume_spike)
    medium: clean breakout or strong mean-reversion zone + liquidity
```

Usage:

* Only symbols with triggers + in universe become **candidates**.
* No trigger = no AI = no noise.

---

## 4. Rule-Based Strategy (Baseline Logic)

These are “pre-AI” suggestions.

```yaml
strategy:
  base_position_pct:
    tier1_majors: 0.02         # 2% NLV
    tier2_liquid_alts: 0.01    # 1% NLV
    tier3_event: 0.005         # 0.5% NLV

  conviction_from_rules:
    strong_move_with_volume: 0.7
    breakout_clean: 0.6
    mean_reversion_extreme: 0.5
    weak_signal: 0.0           # do nothing

  require:
    min_conviction_to_propose: 0.5
    max_open_positions: 12
```

If a candidate can’t reach 0.5+ conviction **from pure math**, don’t even ask the models.

---

## 5. Execution & Microstructure

```yaml
execution:
  default_order_type: "limit_post_only"
  max_slippage_bps: 40                 # 0.4% worst-case from mid
  cancel_after_seconds: 60             # for passive orders
  post_only_ttl_seconds: 4             # cancel resting maker orders quickly if unfilled
  partial_fill_min_pct: 0.25

  spread_checks:
    hard_max_spread_bps: 60
    tighten_if_spread_bps_over: 40     # reduce size if 40–60 bps

  volatility_adjustment:
    high_vol_window_minutes: 60
    high_vol_threshold_pct: 8.0        # 8% move in 1h
    size_reduction_factor: 0.5         # halve size in crazy tape
```

Goal: never be the idiot paying 2–3% edge to get in.

---

## 6. AI Usage (Only Where It’s Worth It)

### Cadence

```yaml
ai:
  enabled: true

  news_fetch:
    full_scan_minutes: 45        # every 45 min
    on_demand_for_triggers: true # yes, for high severity

  m1_fundamental:
    min_trigger_severity: "medium"
    max_candidates_per_run: 10

  m2_quant:
    enabled: true
    max_candidates_per_run: 10

  m3_arbitrator:
    required_min_combined_conviction: 0.6
    max_total_at_risk_pct: 0.15      # cannot exceed global risk
```

### M1 (GPT-5) knobs

```yaml
ai_m1:
  boost_if:
    strong_catalyst: +0.15           # ETF, listing, mainnet, big partnership
  cut_if:
    ambiguous_news: -0.15
    stale_news_hours: 24
```

### M2 (Sonnet) knobs

```yaml
ai_m2:
  veto_if:
    illiquid: true                   # spread/depth fail
    overextended_parabolic: true     # e.g. 30%+ 24h move & thin book
```

### M3 (o3) behavior (conceptual)

* Reject any trade that:

  * Violates risk policy,
  * Lacks evidence for news-based thesis,
  * Conflicts strongly between M1 & M2 with no clear resolution.
* Only approve trades with:

  * `combined_conviction >= 0.6`
  * Fit inside:

    * per-asset,
    * per-theme,
    * total-at-risk bounds.

---

## 7. Run Frequencies

Concrete suggestion:

```yaml
scheduling:
  market_data_heartbeat_seconds: 60        # Coinbase data
  trigger_eval_seconds: 60
  strategy_eval_seconds: 300               # 5 min
  ai_news_full_scan_minutes: 45
  ai_decision_cycle_minutes: 15            # only if candidates exist or scheduled
```

That’s cheap, responsive, and not insane on API / LLM cost.

---

## 8. Sanity: When to Override / Stop

Add explicit guardrails:

```yaml
safety:
  kill_switch: env_flag_or_db_flag
  stop_new_trades_if:
    data_stale_minutes: 5
    news_fetch_errors_in_row: 5
    coinbase_status_not_healthy: true
```

If anything critical is broken → **no new trades, only risk reduction**.

---

## TL;DR

If you want a clean starting set:

* **Per-asset cap:** 5% NLV
* **Total active risk:** 15% NLV
* **Daily stop:** -3%, **weekly:** -7%
* **Universe floor:** $20M+ 24h vol, ≤0.6% spread, decent depth
* **Triggers:** 4%/15m or 6%/60m moves, 2x+ volume spikes, clean breakouts only
* **AI:** used only after triggers; approve trades only if combined conviction ≥0.6 and all risk rules pass.

From here, we tune based on actual logs: if it never trades, loosen triggers / conviction; if it trades dumb, tighten risk / catalysts. If you’d like, next step I can turn this into a ready-to-drop `policy.yaml` + comments.

