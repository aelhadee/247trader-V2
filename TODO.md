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


