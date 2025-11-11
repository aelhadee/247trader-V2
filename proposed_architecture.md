Here’s the architecture. Clean, rule-first, AI where it actually earns oxygen.

I’ll break it into **modules + data flows** so you can map straight onto your repo.

---

## 1. Core Idea

Your system =

> **Deterministic trading engine on Coinbase**
> with **optional AI co-pilot** for news/catalysts/sanity checks.

If all AI dies, the bot still runs safely. If AI works, it upgrades decisions—not replaces them.

---

## 2. Top-Level Blocks

**Core (required):**

1. Config & Policy
2. Exchange & Data Adapter (Coinbase)
3. Universe Manager
4. Trigger Engine
5. Rule-Based Strategy Engine
6. Risk Engine
7. Execution Engine
8. State & Audit Log

**Optional (high-value AI):**

9. News Ingestion & Classifier
10. M1: GPT-5 Fundamental/News Analyst
11. M2: Sonnet Quant/Tape Analyst (optional)
12. M3: o3 Arbitrator / Policy Cop

---

## 3. Module-by-Module

### 3.1 Config & Policy

Single source of truth.

**Files:**

* `config/app.yaml` – infra, intervals, logging, mode.
* `config/policy.yaml` – risk & exposure rules.
* `config/universe.yaml` – allowed assets, tiers, per-asset overrides.

**Key knobs:**

* `max_trades_per_day`
* `max_drawdown_daily_pct`
* `max_total_at_risk_pct`
* `max_per_asset_pct`
* `max_per_theme_pct` (L2s, memes, etc.)
* `liquidity`:

  * `min_24h_volume_usd`
  * `max_spread_bps`
  * `min_depth_usd`
* `ai.enabled`, `ai.max_calls_per_hour`, `ai.min_trigger_severity`

Everything else reads from here. No magic numbers buried in code.

---

### 3.2 Coinbase Adapter

**Module:** `core/exchange/coinbase_client.py`

Responsibilities:

* REST + WebSocket wrappers
* Fetch:

  * tickers
  * order books
  * 24h volume
  * account balances
  * open orders
  * fills
* Place/cancel orders

Return **clean, typed objects**, e.g.:

```python
MarketSnapshot { symbol -> { price, vol24h, ... } }
OrderBook { bids, asks, spread_bps, depth_10bps_usd, ... }
PortfolioState { positions, cash_usd, nlv }
```

No business logic here. Just truth.

---

### 3.3 Universe Manager

**Module:** `core/universe.py`

Input:

* `MarketSnapshot`
* `OrderBook`
* `config/universe.yaml`

Logic:

* Start from static allowlist (Tier 1/Tier 2).
* Apply filters:

  * volume ≥ min_24h_volume_usd
  * spread ≤ max_spread_bps
  * depth ≥ min_depth_usd
  * only assets actually tradable on Coinbase with your profile
* Allow Tier 3 (event-driven) only if later flagged by News/Events module.

Output:

```python
UniverseEntry {
  symbol,
  tier,
  spread_bps,
  vol24h_usd,
  depth_20bps_usd,
  eligible: bool
}
```

If it’s not in `Universe`, it is **impossible** for the bot to trade it. That’s deliberate.

---

### 3.4 Trigger Engine (Rules-Only)

**Module:** `core/triggers.py`

Runs every N minutes (e.g. 1–5m).

Inputs:

* `Universe`
* Latest `MarketSnapshot`
* Short-term history (cache)
* `PortfolioState`

Logic examples:

* Price move:

  * `abs(Δprice_15m) >= X%`
* Volume spike:

  * `vol_1h / vol_24h_avg >= K`
* Breakouts:

  * new 24h / 7d highs/lows
* Volatility regime:

  * realized vol above/below threshold
* Risk flags:

  * per-asset exposure high,
  * cluster exposure high.

Output:

```python
TriggerResult {
  candidates: [ "SOL", "ETH", ... ],
  severity: "low" | "med" | "high",
  reason_map: { "SOL": ["volume_spike", "breakout"], ... }
}
```

If `candidates` empty → **no AI calls, no strategy eval** → run exits cheap.

---

### 3.5 Rule-Based Strategy Engine

**Module:** `strategy/rule_engine.py`

This is your **baseline brain**. Still no AI.

Inputs:

* `TriggerResult`
* `Universe`
* `MarketSnapshot`
* `PortfolioState`
* `policy.yaml`

Logic (examples):

For each candidate:

* If breakout + strong volume & tight spread:

  * propose long with base conviction.
* If extended + exhaustion:

  * propose trim or no new longs.
* If DD or high exposure:

  * prefer de-risking over entries.

Output:

```python
BaseProposal {
  symbol,
  side: "BUY" | "SELL" | "HOLD",
  base_size_fraction_of_nlv,
  conviction_rule: 0–1,
  reasons: ["breakout", "volume_confirmation", ...]
}
```

This alone must be reasonable enough to run as a standalone model.

---

### 3.6 Risk Engine

**Module:** `core/risk.py`

Central cop. No vibes.

Inputs:

* `BaseProposal` (and later AI-enriched proposals)
* `PortfolioState`
* `policy.yaml`
* `StateStore` (pnl_today, trades_today, cooldowns, etc.)

Checks:

* daily / weekly DD
* trades_today vs `max_trades_per_day`
* per-asset / per-theme caps
* kill-switch flags
* cooldown after big loss / cluster of losses

Output:

* Filtered proposals.
* Reasons for rejection logged.

If Risk says no → **it’s no.** AI cannot override.

---

### 3.7 Execution Engine

**Module:** `core/execution.py`

Inputs:

* Approved trades (symbol, side, target size, max_slippage_bps, etc.)
* Live order book from Coinbase

Logic:

* Compute order size (USD & units).
* Check:

  * spread ≤ allowed
  * depth supports size
* Choose order type:

  * default: limit/post-only
  * fallback: aggressive limit within slippage
* Submit via Coinbase client.

Writes:

* Audit log event
* Update StateStore

No LLMs here. Ever.

---

## 4. Optional AI Layer (Where It *Is* Worth It)

These wrap around **candidates** from rules, not the whole market.

### 4.1 News Ingestion

**Module:** `ai/news_fetcher.py`

Cadence:

* Every 30–60 minutes global.
* On-demand for triggered symbols with high severity.

Sources: strict allowlist.
Output: structured `NewsItem`s per symbol.

---

### 4.2 M1 – GPT-5 (Fundamental / Catalyst Analyst)

**Module:** `ai/m1_fundamental.py`

Inputs:

* `NewsItems` per candidate symbol
* `BaseProposal`
* `Trigger reasons`
* `Universe` & `policy` context

Output:

```json
{
  "refined": [
    {
      "symbol": "SOL",
      "side": "BUY",
      "sentiment": 0.7,
      "thesis_quality": 0.75,
      "catalyst_type": "upgrade/listing/etc",
      "adjusted_size_factor": 1.2,
      "reject": false,
      "red_flags": []
    }
  ]
}
```

Rules:

* If news is weak/stale → downgrade or reject.
* If catalyst is real & strong → slightly boost conviction/size (within caps).

---

### 4.3 M2 – Sonnet (Quant / Sanity Cross-Check) [Optional]

**Module:** `ai/m2_quant.py`

Inputs:

* Price history
* Depth, spread, volatility

Output:

* Flags:

  * overextended
  * illiquid
  * structurally broken
* Adjusted size multiplier / reject.

If you already have solid quant rules, this is “nice to have”, not critical.

---

### 4.4 M3 – o3 Arbitrator / Policy Cop

**Module:** `ai/m3_arbitrator.py`

Inputs:

* `BaseProposal` (rules)
* `M1` refinements
* `M2` flags (if enabled)
* `NewsItems`
* `RiskPolicy`
* `PortfolioState`
* `Universe`

Responsibilities:

1. Schema & sanity:

   * reject malformed outputs.
   * ensure only universe symbols.
2. Evidence check:

   * if M1 cites catalysts, confirm they exist in `NewsItems`.
3. Policy enforcement (in addition to Risk Engine):

   * no overrides of hard caps.
   * no trades without clear justification.
4. Resolve:

   * conflicting views between rules/M1/M2.

Output:

```json
{
  "trades": [
    {
      "symbol": "SOL",
      "side": "BUY",
      "size_fraction_of_nlv": 0.01,
      "max_slippage_bps": 40,
      "time_horizon_hours": 24,
      "stop_loss_pct": 6,
      "take_profit_pct": 15,
      "reasons": [
        "Rule-based breakout + volume",
        "Confirmed catalyst from allowlisted sources",
        "Within risk and liquidity limits"
      ]
    }
  ],
  "no_trade_reason_if_empty": "All candidates failed conviction or policy checks."
}
```

If `trades` is empty → explicit `no_trade_reason_if_empty` is logged.

Execution Engine then acts only on this.

---

## 5. State & Audit Log

**Module:** `infra/state_store.py`, `infra/audit_log.py`

Store:

* pnl_today / pnl_history
* trades_today count
* cooldown flags
* last_decisions (for debugging)
* config version / model versions used for each trade

Every execution cycle logs one structured record:

```json
{
  "timestamp": "...",
  "universe_size": 34,
  "candidates": ["SOL", "ARB"],
  "base_proposals": [...],
  "m1_refined": [...],
  "m2_flags": [...],
  "final_trades": [...],
  "no_trade_reason": null
}
```

So when something looks off, you don’t guess.

---

## 6. Execution Modes

Global `MODE` in config:

* `DRY_RUN` – log-only, no orders.
* `PAPER` – simulate fills with live quotes.
* `LIVE` – real orders.

All modules read this. Arbitrator + Execution must **refuse** to place real trades unless `MODE=LIVE`.

---

## TL;DR

* Yes, you can (and should) run a **continuous Coinbase bot** on:

  * strict universe,
  * hard rules,
  * real risk engine,
  * boring, safe execution.
* AI is:

  * optional,
  * focused,
  * only on candidates,
  * never allowed to bypass risk/policy.

Short answer: **it’s very close, but not quite “hand it to devs and walk away” ready.**

It’s ~80–85% there as an architecture doc. To make it buildable with minimal “uhh what did they mean?”, you should tighten a few things:

Let me plug the gaps for you now so this becomes implementation-grade.

---

## 1. What’s already solid ✅

Your doc is **clear enough** on:

* Core philosophy: rules-first, AI-optional, AI never bypasses risk.
* Major modules and responsibilities.
* Separation between:

  * Coinbase adapter,
  * Universe,
  * Triggers,
  * Rule engine,
  * Risk,
  * Execution,
  * AI add-ons.
* Execution modes (DRY_RUN / PAPER / LIVE).
* Auditability requirement.

A competent senior engineer will read this and say: “This is sane.”

Now let’s make it unambiguous.

---

## 2. What’s missing (and I’ll fill it in)

You need 6 extra bits to make it “just implement”:

1. **Explicit data models**
2. **Main loop & scheduling contract**
3. **Config skeleton with concrete keys**
4. **Error handling + safety rules**
5. **Testing/backtest expectations**
6. **AI contracts (schema + failure behavior)**

I’ll give each briefly and cleanly.

---

## 3. Core Data Models (lock these in)

Add this section under your architecture so devs don’t improvise.

```python
# core/types.py

from typing import Dict, List, Literal, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class MarketSnapshot:
    timestamp: datetime
    prices: Dict[str, float]            # symbol -> last price
    vol24h_usd: Dict[str, float]        # symbol -> 24h volume in USD

@dataclass
class OrderBookStats:
    spread_bps: float
    depth_20bps_usd: float

@dataclass
class UniverseEntry:
    symbol: str
    tier: Literal["tier1", "tier2", "tier3"]
    spread_bps: float
    vol24h_usd: float
    depth_20bps_usd: float
    eligible: bool

@dataclass
class PortfolioState:
    timestamp: datetime
    cash_usd: float
    positions: Dict[str, float]         # symbol -> units
    prices: Dict[str, float]            # symbol -> price used
    nlv_usd: float                      # net liquidation value

@dataclass
class TriggerResult:
    candidates: List[str]
    severity: Literal["low", "med", "high"]
    reason_map: Dict[str, List[str]]    # symbol -> reasons

@dataclass
class BaseProposal:
    symbol: str
    side: Literal["BUY", "SELL", "HOLD"]
    base_size_fraction_of_nlv: float
    conviction_rule: float              # 0..1
    reasons: List[str]

@dataclass
class FinalTrade:
    symbol: str
    side: Literal["BUY", "SELL"]
    size_fraction_of_nlv: float
    max_slippage_bps: float
    time_horizon_hours: int
    stop_loss_pct: float
    take_profit_pct: float
    reasons: List[str]
```

This stops 90% of misinterpretation.

---

## 4. Main Loop & Scheduling (make it explicit)

Drop this into `runner/main_loop.py` section in the doc:

```python
def run_cycle():
    # 1. Data
    mkt = coinbase.get_market_snapshot()
    ob_stats = coinbase.get_orderbooks_for_universe_candidates()
    portfolio = coinbase.get_portfolio_state(mkt.prices)

    # 2. Universe
    universe = UniverseBuilder.build(mkt, ob_stats, universe_cfg)

    # 3. Triggers
    triggers = TriggerEngine.run(universe, mkt, portfolio, history_cache)

    if not triggers.candidates:
        Audit.log_cycle(universe, triggers, [], [], [], [], reason="no_candidates")
        return

    # 4. Rule-based strategy
    base_proposals = RulesEngine.propose(triggers, universe, mkt, portfolio, policy)

    # 5. Risk filter
    risk_filtered = RiskEngine.filter(base_proposals, portfolio, policy, state_store)

    if ai_enabled and risk_filtered:
        # 6. Optional AI refinement
        news = NewsFetcher.fetch_if_needed(risk_filtered, triggers)
        m1 = M1_Fundamental.refine(risk_filtered, news, policy)
        m2 = M2_Quant.check(risk_filtered, mkt, ob_stats) if m2_enabled else None
        final_trades = M3_Arbitrator.decide(
            base=risk_filtered, m1=m1, m2=m2,
            news=news, policy=policy, portfolio=portfolio, universe=universe
        )
    else:
        final_trades = [to_final_from_base(p) for p in risk_filtered]

    # 7. Execution
    executed = ExecutionEngine.execute(final_trades, portfolio, mkt, mode)

    # 8. Logging & state
    Audit.log_cycle(universe, triggers, base_proposals, risk_filtered, final_trades, executed)
    StateStore.update(executed, portfolio)
```

Scheduling recommendation (you can include):

* `run_cycle`: every **5 min**
* `NewsFetcher`: every **45 min** + on-demand for high severity triggers.

---

## 5. Config Skeleton (so they don’t guess)

Append a minimal example:

```yaml
# config/app.yaml
mode: DRY_RUN        # DRY_RUN | PAPER | LIVE
loop_interval_seconds: 300
exchange: coinbase
logging:
  level: INFO
  json: true

# config/universe.yaml
tier1:
  symbols: [BTC-USD, ETH-USD, SOL-USD]
tier2:
  auto: true
  min_24h_volume_usd: 20000000
  max_spread_bps: 60
  min_depth_20bps_usd: 50000
tier3:
  enabled: true      # only via events/news
blacklist: [BTC3L-USD, BTC3S-USD]

# config/policy.yaml
risk:
  max_total_at_risk_pct: 0.15
  max_per_asset_pct: 0.05
  max_per_theme_pct:
    L2: 0.10
    MEME: 0.05
  max_trades_per_day: 10
  daily_stop_pnl_pct: -0.03
  weekly_stop_pnl_pct: -0.07
  min_trade_notional_usd: 100

liquidity:
  max_spread_bps: 60
  min_24h_volume_usd: 20000000
  min_depth_20bps_usd: 50000

ai:
  enabled: true
  min_trigger_severity: med
  max_calls_per_hour: 40
```

This prevents random key naming and “oh I thought it was percent vs decimal” bugs.

---

## 6. AI Contracts & Failure Rules

Your doc hints at them; make it explicit:

* **M1/M2/M3 must:**

  * Return **valid JSON only** matching schema.
  * Never introduce new symbols.
  * Treat any parsing/validation error as: **ignore that model’s output**.

* **Arbitrator rules:**

  1. If `MODE != LIVE` → never send real orders.
  2. If any hard risk rule would be broken → `trades = []` with reason.
  3. If all candidates rejected → log `no_trade_reason_if_empty`.
  4. AI outputs are *suggestions*; Risk + Execution are final.

Write one line in the doc:

> “On any AI/tool error, fall back to rules-only or NO_TRADE; never to unsafe behavior.”

That’s the guardrail.

---

## 7. Testing & Backtesting (1 paragraph is enough)

Add:

* Unit tests for:

  * Universe filters,
  * Trigger logic,
  * Risk constraints,
  * Sizing + slippage rules.
* A simple backtest harness:

  * Replays historical candles/volume,
  * Runs `run_cycle` (without AI),
  * Outputs trades & PnL.

And one requirement:

> “Rules-only strategy MUST be backtested and make sense before AI is enabled.”

That’s it. Devs will know what to do.

---

## Answer to your question

Yes, with the additions above (data models + main loop + config + AI contracts + safety), this becomes **detailed enough** for a competent team to implement v2 without guessing your intent.

You’re not overdoing it; this is the right level: opinionated, modular, buildable.

**TL;DR:** You’re 1–2 pages of concrete types + loops away from a fully dev-ready spec. I’ve just given you those pieces—ship this bundle to your devs and tell them “implement exactly this.”

