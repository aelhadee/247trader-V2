- [x] Inspect current execution and risk code paths for logging, fill reconciliation, cancel handling, and open-position counting.
- [x] Write failing regression tests capturing base/quote fill mismatch, canceled→filled reconciliation, and risk cap counting of open orders.
- [ ] Update execution engine logging to distinguish order placement from fills, and make cancel races tolerant.
- [x] Fix fill aggregation math and state persistence using base units and average price.
- [x] Extend risk engine to count open orders toward max position caps, support dust thresholds, and allow adds while saturated.
- [x] Harden risk cap filter to auto-approve adds vs new symbols per policy toggles.
- [ ] Adjust configuration defaults (TTL, min notional, new flags) and keep schema/tests/docs in sync.
- [ ] Implement taker promotion gating and cancel retry/backoff scaffolding per new policy toggles.
- [x] Plumb per-proposal rejection reasons through risk checks and runner logging.
- [ ] Raise execution min_notional_usd and add dust sweep guidance to policy/tests.
- [x] Run full relevant test suite (pytest) to confirm fixes.
- [x] Prepare rollback guidance and update TODO checklist.
- [ ] Ship rebuild_positions maintenance utility to repair historical base units.



Short version: for your setup, I’d aim for ~10–15s TTL for normal maker orders and ~20–30s TTL for purge-only TWAP slices on junk (T3) coins. Right now your 5–12s is a bit too tight for illiquid stuff like PENGU/ZK.

How TTL actually bites you right now

From the logs:

PENGU TTLs: 6s and 5s

ZK TTLs: 12s and 9s

Pattern:

You place a post-only limit slightly off the best ask.

You wait a few seconds.

Nothing trades into you.

TTL fires, you cancel, and the purge slice is marked failed.

Bot complains: “residual ~$16.19… purge sell failed”

This means your post-only + micro TTL combo is too strict for illiquid books. You’re giving the market almost no time to rotate into your resting order.

Trade-offs: how long should TTL be?

Let’s ground it in your actual loop:

Main loop interval: 60s

A purge slice currently:

Places a post-only limit

Blocks until TTL expires and cancel happens

Then moves on

So you need a TTL that:

Gives the order a real chance to fill.

Doesn’t blow your cycle time way past 60s when there are 2–3 purge assets.

For normal entries/exits on decent liquidity (T1/T2)

You care more about:

Not sitting stale orders forever

Keeping the loop responsive

Good range: 10–15 seconds

Long enough for a few book-refresh cycles

Short enough that even a couple of orders won’t wreck your 60s interval

I’d keep those around where you already are: ~12–15s.

For purge of illiquid trash (T3, tiny notional)

Here, the priority is different:

“Just get me out of this meme garbage”

“And please don’t block forever or go full market ape.”

You’re using:

Small dollar amount (like $15)

Maker-only

TWAP slice per cycle

Given that:

Risk of adverse selection is low (you want out anyway, and post-only means you don’t cross).

The bigger risk is you never fill and keep carrying dead weight.

So for purge-only TWAP slices, I’d use:

TTL = 20–30 seconds per attempt for T3 coins

e.g. 25s is a good compromise.

That still leaves ~30–35s in the 60s loop for:

Universe building

Triggers

Rules

Risk

Any other execution

Even with 2 purge assets, you’re roughly okay:

2 × 25s TTL + ~10–15s other work ≈ 60–65s → slightly over, but your backoff logic already handles the occasional >100% utilization.

If you want to be super predictable on cycle time, cap purge TTL at 20s, but 25–30s will materially help fills.

One important nuance: TTL isn’t your only problem

Your purge orders are also:

Not at the best ask; they’re slightly above it (to guarantee post-only).

On illiquid books, that means:

Best ask sits in front of you.

Volume trades at that best ask.

Your order never becomes top-of-book within a 5–12s window.

TTL fires and cancels.

So even with better TTL, you may still want to:

Be more aggressive on price for purge mode, not just TTL:

Allow placing at current best ask (still maker, still post-only-friendly).

Only use extra “cushion ticks” if you’re getting post-only rejections.

But since you asked specifically about TTL: upping it helps, it’s just not the full story.

Concrete recommendation for your config

Given:

Loop interval: 60s

Current issues: purge not filling; cycle utilization hit ~110%

I’d do:

Normal maker orders (T1/T2 entries/exits):

post_only_ttl ≈ 12–15s

Purge TWAP for T3 / ineligible junk:

purge_post_only_ttl ≈ 20–30s

Start with 25s and see if fills materially improve.

If utilization regularly >120%:

Drop purge TTL to 20s or limit to 1 purge asset per cycle.

TL;DR

Your current 5–12s TTL is too short for illiquid purge sells.

Keep normal trades around 10–15s TTL.

Use 20–30s TTL for purge-only TWAP slices on junk coins (I’d start at 25s).

But also know: price placement is just as important as TTL — sitting above the best ask with a short TTL is basically a guaranteed non-fill.