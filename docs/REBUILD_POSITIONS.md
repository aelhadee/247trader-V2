# Rebuilding Position Quantities

The `scripts/rebuild_positions.py` utility repairs the position section inside `data/.state.json` by replaying recent Coinbase fills and recomputing base units, entry prices, and USD exposure. Use it whenever:

- a state backup was restored without base-unit fields,
- execution crashed while fills were still settling and the portfolio drifted,
- you need an audit trail of how many base units were acquired over the last N hours.

It **never places orders**. The Coinbase client is instantiated with `read_only=True` and only uses historical fill data.

## Prerequisites

1. **Kill switch**: touch `data/KILL_SWITCH` so the main loop cannot submit new trades while you patch the state.
2. **Back up state**: copy `data/.state.json` to `data/state_backups/` before running the script.
3. **API credentials**: reuse the same environment variables/secret file already configured for the bot. The script needs access to the fills endpoint even in DRY_RUN mode.

## Usage

| Argument | Default | Description |
| --- | --- | --- |
| `--hours` | `48` | Lookback window for fills. Increase (e.g., 168) if the account was idle for a few days. |
| `--state` | `data/.state.json` | Optional override for the state file. |
| `--dry-run` | `False` | When set, prints a JSON summary and a truncated state preview instead of writing the file. |

### Recommended workflow

```bash
# 1. Dry-run to inspect changes (no file writes)
python scripts/rebuild_positions.py --hours 72 --state data/.state.json --dry-run > rebuild_preview.json

# 2. Review rebuild_preview.json (positions_updated, removed, sample state)

# 3. Apply the rebuild once satisfied
python scripts/rebuild_positions.py --hours 72 --state data/.state.json
```

Typical summary output:

```json
{
  "symbols_seen": 5,
  "positions_updated": 3,
  "positions_removed": 1
}
```

After the script runs (non-dry mode) it persists the updated state via `infra.state_store.StateStore.save()`, so all downstream consumers (risk engine, portfolio reconciler, audit log) immediately pick up the repaired quantities.

## Safety & Rollback

- Every invocation should start from a **fresh copy of the current state file**. If something looks off, restore the backup and rerun with a different `--hours` window.
- Because state writes are atomic, the previous file is preserved as `.state.json.bak`. Keep that backup until you confirm the rebuilt values in logs/audit.
- The script respects the policy kill switch and does not mutate open orders; it only fixes the `positions` map.

## Tests & Monitoring

- Regression coverage lives in `tests/test_scripts_rebuild_positions.py` (aggregation math, dry-run preview, state prune).
- After running the script, tails the main loop logs or run `python runner/main_loop.py --once --mode DRY_RUN` to ensure the risk engine sees the corrected quantities.

For additional context on fill reconciliation, see `docs/FILL_RECONCILIATION.md`.
