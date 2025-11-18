"""
247trader-v2 Core: Universe Manager

Loads universe config and enforces eligibility rules.
Salvaged from v1 but simplified and hardened.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging

from core.exchange_coinbase import get_exchange, Quote

logger = logging.getLogger(__name__)


@dataclass
@dataclass
class UniverseAsset:
    """Asset in the trading universe"""
    symbol: str
    tier: int  # 1 = core, 2 = rotational, 3 = event-driven
    allocation_min_pct: float
    allocation_max_pct: float
    volume_24h: float
    spread_bps: float
    depth_usd: float
    eligible: bool
    ineligible_reason: Optional[str] = None
    eligibility_reason: Optional[str] = None


@dataclass
class UniverseSnapshot:
    """Snapshot of eligible universe at a point in time"""
    timestamp: datetime
    regime: str
    tier_1_assets: List[UniverseAsset]
    tier_2_assets: List[UniverseAsset]
    tier_3_assets: List[UniverseAsset]
    excluded_assets: List[str]
    total_eligible: int

    def get_all_eligible(self) -> List[UniverseAsset]:
        """Get all eligible assets across tiers"""
        return self.tier_1_assets + self.tier_2_assets + self.tier_3_assets

    def get_asset(self, symbol: str) -> Optional[UniverseAsset]:
        """Get specific asset by symbol"""
        for asset in self.get_all_eligible():
            if asset.symbol == symbol:
                return asset
        return None


class UniverseManager:
    """
    Manages trading universe with tier-based eligibility.

    Responsibilities:
    - Load universe config
    - Apply liquidity filters
    - Apply regime adjustments
    - Track excluded assets
    """

    def __init__(self, config: dict, exchange=None, state_store=None, alert_service=None):
        self.config = config
        self.exchange = exchange
        self.state_store = state_store
        self.alert_service = alert_service

        # Initialize cache attributes (fix for AttributeError)
        self._cache: Optional[UniverseSnapshot] = None
        self._cache_time: Optional[datetime] = None
        refresh_hours = config.get('universe', {}).get('refresh_interval_hours', 1)
        self._cache_ttl = timedelta(hours=refresh_hours)

        # Initialize near-threshold configuration
        self._near_threshold_cfg = config.get('universe', {}).get('near_threshold_override', {})
        self._near_threshold_usage: dict[str, int] = {}

        # OPTIMIZATION: Product list caching to reduce rate limit warnings
        # Cache list_products() calls across cycles/steps (expensive API call)
        self._products_cache: Optional[List[str]] = None
        self._products_cache_time: Optional[datetime] = None
        # Cache TTL: 5 minutes (products rarely change intraday)
        products_cache_minutes = config.get('universe', {}).get('products_cache_minutes', 5)
        self._products_cache_ttl = timedelta(minutes=products_cache_minutes)
        logger.info(f"Product list cache enabled: TTL={products_cache_minutes}min")

    @classmethod
    def from_config_path(cls, config_path: str, exchange=None, state_store=None, alert_service=None):
        """
        Create UniverseManager from config file path (backward compatibility).

        Args:
            config_path: Path to config file
            exchange: Optional exchange instance
            state_store: Optional state store instance
            alert_service: Optional alert service instance

        Returns:
            UniverseManager instance
        """
        from pathlib import Path
        import yaml

        config_path_obj = Path(config_path)
        if not config_path_obj.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path_obj) as f:
            config = yaml.safe_load(f)

        return cls(config=config, exchange=exchange, state_store=state_store, alert_service=alert_service)

    def _load_config(self) -> dict:
        """
        Load universe configuration (uses self.config directly).
        Note: This method now just returns self.config since it's passed in constructor.
        Kept for backward compatibility with any internal callers.
        """
        # Check if dynamic mode is enabled
        universe_config = self.config.get('universe', {})
        if universe_config.get('method') == 'dynamic_discovery':
            logger.info("Using dynamic universe discovery from Coinbase")
            config = self._build_dynamic_universe(self.config)
            return config

        logger.info(f"Loaded universe config with {len(self.config.get('tiers', {}))} tiers")
        return self.config

    def _build_dynamic_universe(self, config: dict) -> dict:
        """
        Dynamically discover tradable assets from Coinbase.

        Fetches all USD pairs and categorizes them into tiers based on:
        - Market cap proxy (volume)
        - Liquidity metrics
        - Exchange support
        """
        # OPTIMIZATION: Check products cache first to avoid redundant API calls
        symbols = self._get_cached_products()

        if symbols:
            logger.info(f"Using cached product list ({len(symbols)} symbols)")
        else:
            logger.info("Fetching all tradable pairs from Coinbase...")

            try:
                exchange = get_exchange()
                symbols = exchange.get_symbols()

                # CRITICAL: Treat empty symbols as failure
                if not symbols:
                    raise RuntimeError("No symbols returned from Coinbase – triggering fallback")

                # Cache successful fetch
                self._update_products_cache(symbols)
            except Exception as e:
                logger.error(f"Failed to fetch symbols from Coinbase: {e}")
                # Will trigger fallback below if symbols is empty
                symbols = []

        # Skip further processing if we have no symbols (will trigger fallback)
        if not symbols:
            raise RuntimeError("No symbols available – triggering fallback")

        try:
            # Filter to USD pairs only
            usd_pairs = [s for s in symbols if s.endswith('-USD')]
            logger.info(f"Found {len(usd_pairs)} USD trading pairs")

            # CRITICAL: Treat empty USD pairs as failure
            if not usd_pairs:
                raise RuntimeError("No USD pairs found – triggering fallback")

            # Get current config for thresholds
            universe_config = config.get('universe', {})
            dynamic_config = universe_config.get('dynamic_config', {})

            # Volume thresholds for tiering
            tier1_min_volume = dynamic_config.get('tier1_min_volume_usd', 100_000_000)  # $100M
            tier2_min_volume = dynamic_config.get('tier2_min_volume_usd', 20_000_000)   # $20M
            tier3_min_volume = dynamic_config.get('tier3_min_volume_usd', 5_000_000)    # $5M

            # Fetch product data for all pairs
            tier1_symbols = []
            tier2_symbols = []
            tier3_symbols = []

            for symbol in usd_pairs[:50]:  # Limit to first 50 to avoid rate limits
                try:
                    quote = exchange.get_quote(symbol)

                    # Categorize by 24h volume
                    if quote.volume_24h >= tier1_min_volume:
                        tier1_symbols.append(symbol)
                    elif quote.volume_24h >= tier2_min_volume:
                        tier2_symbols.append(symbol)
                    elif quote.volume_24h >= tier3_min_volume:
                        tier3_symbols.append(symbol)

                except Exception as e:
                    logger.debug(f"Failed to fetch {symbol}: {e}")
                    continue

            logger.info(f"Dynamic universe: {len(tier1_symbols)} tier1, {len(tier2_symbols)} tier2, {len(tier3_symbols)} tier3")

            # CRITICAL: Treat empty tier 1 as failure
            if not tier1_symbols:
                raise RuntimeError("Dynamic discovery produced empty tier 1 – triggering fallback")

            # Update config with discovered symbols
            if 'tiers' not in config:
                config['tiers'] = {}

            config['tiers']['tier_1_core'] = {
                'symbols': tier1_symbols,
                'constraints': config.get('tiers', {}).get('tier_1_core', {}).get('constraints', {
                    'min_allocation_pct': 5.0,
                    'max_allocation_pct': 40.0,
                    'min_24h_volume_usd': tier1_min_volume,
                    'max_spread_bps': 30
                })
            }

            config['tiers']['tier_2_rotational'] = {
                'symbols': tier2_symbols,
                'constraints': config.get('tiers', {}).get('tier_2_rotational', {}).get('constraints', {
                    'min_allocation_pct': 2.0,
                    'max_allocation_pct': 20.0,
                    'min_24h_volume_usd': tier2_min_volume,
                    'max_spread_bps': 50
                }),
                'filters': ['volume_spike', 'trend_strength', 'regime_adjusted']
            }

            config['tiers']['tier_3_event_driven'] = {
                'symbols': tier3_symbols[:10],  # Limit tier 3
                'constraints': config.get('tiers', {}).get('tier_3_event_driven', {}).get('constraints', {
                    'min_allocation_pct': 1.0,
                    'max_allocation_pct': 10.0,
                    'min_24h_volume_usd': tier3_min_volume,
                    'max_spread_bps': 100
                })
            }

            return config

        except Exception as e:
            logger.error(f"Failed to build dynamic universe: {e}")
            logger.warning("Falling back to static LAYER1 universe from config")

            # Fallback: use static LAYER1 assets from cluster definitions
            layer1_symbols = config.get('clusters', {}).get('definitions', {}).get('LAYER1', [])

            if not layer1_symbols:
                # Ultimate fallback: major cryptos
                layer1_symbols = ['BTC-USD', 'ETH-USD', 'SOL-USD']
                logger.warning(f"No LAYER1 cluster defined, using hardcoded fallback: {layer1_symbols}")

            # Build minimal tier-1 config with fallback symbols
            if 'tiers' not in config:
                config['tiers'] = {}

            config['tiers']['tier_1_core'] = {
                'symbols': layer1_symbols,
                'constraints': config.get('tiers', {}).get('tier_1_core', {}).get('constraints', {
                    'min_allocation_pct': 5.0,
                    'max_allocation_pct': 40.0,
                    'min_24h_volume_usd': 50_000_000,
                    'max_spread_bps': 30
                }),
                'refresh': 'weekly'
            }

            # Empty tier 2 and 3 in offline mode
            config['tiers']['tier_2_rotational'] = {'symbols': [], 'constraints': {}}
            config['tiers']['tier_3_event_driven'] = {'symbols': [], 'constraints': {}}

            logger.info(f"Offline fallback universe: {len(layer1_symbols)} core assets")
            return config

    def get_universe(self, regime: str = "chop", 
                     force_refresh: bool = False) -> UniverseSnapshot:
        """
        Get eligible universe snapshot for given regime.

        Args:
            regime: "bull" | "chop" | "bear" | "crash"
            force_refresh: Skip cache and rebuild

        Returns:
            UniverseSnapshot with eligible assets
        """
        # Check cache
        if not force_refresh and self._is_cache_valid(regime):
            cache_time = self._cache_time
            if cache_time and cache_time.tzinfo is None:
                cache_time = cache_time.replace(tzinfo=timezone.utc)
            age = None
            if cache_time:
                age = datetime.now(timezone.utc) - cache_time
            logger.debug(f"Using cached universe (age: {age})")
            return self._cache

        logger.info(f"Building universe snapshot for regime={regime}")

        exchange = get_exchange()
        self._near_threshold_usage = {"tier1": 0, "tier2": 0, "tier3": 0}

        # Get tier definitions
        tiers_config = self.config.get("tiers", {})
        liquidity_config = self.config.get("liquidity", {})
        regime_mods = self.config.get("regime_modifiers", {}).get(regime, {})
        exclusions = self.config.get("exclusions", {})

        # Get exclusions first
        excluded = set(exclusions.get("never_trade", []))

        # Add red-flag banned symbols from StateStore
        from infra.state_store import get_state_store
        try:
            state_store = get_state_store()
            red_flag_banned = state_store.get_red_flag_banned_symbols()
            for symbol, ban_info in red_flag_banned.items():
                excluded.add(symbol)
                logger.warning(
                    f"🚩 Excluding red-flagged asset: {symbol} "
                    f"(reason: {ban_info['reason']}, expires: {ban_info['expires_at_iso']})"
                )
        except Exception as exc:
            logger.warning(f"Failed to load red flag bans from StateStore: {exc}")

        # Build tier 1 (core)
        tier_1 = self._build_tier_1(
            tiers_config.get("tier_1_core", {}),
            liquidity_config,
            regime_mods,
            exchange,
            excluded
        )

        # Build tier 2 (rotational)
        tier_2 = self._build_tier_2(
            tiers_config.get("tier_2_rotational", {}),
            liquidity_config,
            regime_mods,
            exchange,
            excluded
        )

        # Build tier 3 (event-driven)
        tier_3 = self._build_tier_3(
            tiers_config.get("tier_3_event_driven", {}),
            liquidity_config,
            regime_mods
        )

        snapshot = UniverseSnapshot(
            timestamp=datetime.now(timezone.utc),
            regime=regime,
            tier_1_assets=tier_1,
            tier_2_assets=tier_2,
            tier_3_assets=tier_3,
            excluded_assets=list(excluded),
            total_eligible=len(tier_1) + len(tier_2) + len(tier_3)
        )

        # Alert on empty universe (CRITICAL operational issue)
        eligible_count = len(tier_1) + len(tier_2) + len(tier_3)
        min_eligible = self.config.get("universe", {}).get("min_eligible_assets", 2)
        if eligible_count < min_eligible:
            if hasattr(self, 'alert_service') and self.alert_service:
                from infra.alerting import AlertSeverity

                # Collect ineligibility reasons
                ineligible_reasons = {}
                for symbol, asset_list in [("tier1", tier_1), ("tier2", tier_2), ("tier3", tier_3)]:
                    for asset in asset_list:
                        if not asset.eligible and asset.ineligible_reason:
                            reason = asset.ineligible_reason
                            ineligible_reasons[reason] = ineligible_reasons.get(reason, 0) + 1

                self.alert_service.notify(
                    severity=AlertSeverity.CRITICAL,
                    title="🚨 Empty Universe",
                    message=f"Only {eligible_count} eligible assets (minimum: {min_eligible}) - trading halted",
                    context={
                        "eligible_count": eligible_count,
                        "threshold": min_eligible,
                        "excluded_count": len(excluded),
                        "ineligibility_reasons": ineligible_reasons,
                        "regime": regime,
                        "action": "trading_paused"
                    }
                )
                logger.error(f"🚨 EMPTY UNIVERSE: {eligible_count}/{min_eligible} eligible assets")

        # Cache result
        self._cache = snapshot
        self._cache_time = datetime.now(timezone.utc)

        logger.info(
            f"Universe snapshot: {len(tier_1)} core, {len(tier_2)} rotational, "
            f"{len(tier_3)} event-driven, {len(excluded)} excluded"
        )

        return snapshot

    def _build_tier_1(self, tier_config: dict, liquidity_config: dict,
                      regime_mods: dict, exchange, excluded_symbols: set = None) -> List[UniverseAsset]:
        """Build tier 1 (core) assets"""
        symbols = tier_config.get("symbols", [])
        constraints = tier_config.get("constraints", {})
        excluded_symbols = excluded_symbols or set()

        assets = []
        for symbol in symbols:
            # Skip excluded assets
            if symbol in excluded_symbols:
                logger.info(f"Skipping excluded asset: {symbol}")
                continue
            try:
                # Get market data
                quote = exchange.get_quote(symbol)
                orderbook = exchange.get_orderbook(symbol)

                # Check liquidity
                eligible, reason, eligibility_reason = self._check_liquidity(
                    quote, orderbook, liquidity_config, constraints, tier=1
                )

                # Apply regime modifier
                multiplier = regime_mods.get("tier_1_multiplier", 1.0)

                asset = UniverseAsset(
                    symbol=symbol,
                    tier=1,
                    allocation_min_pct=constraints.get("min_allocation_pct", 5.0) * multiplier,
                    allocation_max_pct=constraints.get("max_allocation_pct", 40.0) * multiplier,
                    volume_24h=quote.volume_24h,
                    spread_bps=quote.spread_bps,
                    depth_usd=orderbook.total_depth_usd,
                    eligible=eligible,
                    ineligible_reason=reason,
                    eligibility_reason=eligibility_reason,
                )

                if eligible:
                    assets.append(asset)
                else:
                    logger.warning(f"Tier 1 asset {symbol} ineligible: {reason}")

            except Exception as e:
                logger.warning(f"Failed to process tier 1 asset {symbol}: {e} - using fallback data")
                # Offline fallback: create asset with neutral metrics
                multiplier = regime_mods.get("tier_1_multiplier", 1.0)
                asset = UniverseAsset(
                    symbol=symbol,
                    tier=1,
                    allocation_min_pct=constraints.get("min_allocation_pct", 5.0) * multiplier,
                    allocation_max_pct=constraints.get("max_allocation_pct", 40.0) * multiplier,
                    volume_24h=100_000_000.0,  # Assume $100M volume (meets tier 1 criteria)
                    spread_bps=20.0,  # Assume tight spread
                    depth_usd=1_000_000.0,  # Assume $1M depth
                    eligible=True,  # Mark as eligible in offline mode
                    ineligible_reason=None,
                    eligibility_reason="fallback_offline",
                )
                assets.append(asset)
                logger.info(f"Added {symbol} with fallback data (offline mode)")

        return assets

    def _build_tier_2(self, tier_config: dict, liquidity_config: dict,
                      regime_mods: dict, exchange, excluded_symbols: set = None) -> List[UniverseAsset]:
        """Build tier 2 (rotational) assets"""
        symbols = tier_config.get("symbols", [])
        constraints = tier_config.get("constraints", {})
        filters = tier_config.get("filters", [])
        excluded_symbols = excluded_symbols or set()

        # In offline mode, skip tier 2 to speed up tests
        if not symbols:
            return []

        assets = []
        for symbol in symbols:
            # Skip excluded assets
            if symbol in excluded_symbols:
                logger.info(f"Skipping excluded asset: {symbol}")
                continue

            try:
                # Get market data
                quote = exchange.get_quote(symbol)
                orderbook = exchange.get_orderbook(symbol)

                # Check liquidity
                eligible, reason, eligibility_reason = self._check_liquidity(
                    quote, orderbook, liquidity_config, constraints, tier=2
                )

                if not eligible:
                    # Use INFO instead of DEBUG for better visibility
                    logger.info(f"Tier 2 asset {symbol} ineligible: {reason}")
                    continue

                # Apply additional filters
                if "volume_spike" in filters:
                    # TODO: Check if volume > 1.5x average
                    pass

                if "trend_strength" in filters:
                    # TODO: Check trend indicators
                    pass

                # Apply regime modifier
                multiplier = regime_mods.get("tier_2_multiplier", 1.0)

                # If multiplier is 0, skip this asset
                if multiplier == 0.0:
                    continue

                asset = UniverseAsset(
                    symbol=symbol,
                    tier=2,
                    allocation_min_pct=constraints.get("min_allocation_pct", 2.0) * multiplier,
                    allocation_max_pct=constraints.get("max_allocation_pct", 20.0) * multiplier,
                    volume_24h=quote.volume_24h,
                    spread_bps=quote.spread_bps,
                    depth_usd=orderbook.total_depth_usd,
                    eligible=True,
                    ineligible_reason=None,
                    eligibility_reason=eligibility_reason,
                )

                assets.append(asset)

            except Exception as e:
                logger.debug(f"Skipping tier 2 asset {symbol} (offline): {e}")
                # Skip tier 2 assets in offline mode - not critical for testing

        return assets

    def _build_tier_3(self, tier_config: dict, liquidity_config: dict,
                      regime_mods: dict) -> List[UniverseAsset]:
        """Build tier 3 (event-driven) assets"""
        # TODO: Implement event detection
        # For now, return empty list
        return []

    def _apply_near_threshold_override(
        self,
        *,
        symbol: str,
        tier: int,
        metric: str,
        metric_value: float,
        floor: float,
        reason_code: str,
    ) -> Optional[str]:
        """Apply global near-threshold tolerance if within configured band."""

        cfg = self._near_threshold_cfg
        if not cfg.get("override_enabled", False):
            return None

        tolerance_pct = float(cfg.get("tolerance_pct", 0.1) or 0.0)
        if tolerance_pct < 0:
            tolerance_pct = 0.0

        threshold = floor * (1.0 - tolerance_pct)
        if metric_value < threshold:
            return None

        tier_key = f"tier{tier}"
        max_overrides = cfg.get("max_overrides_per_tier")

        if isinstance(max_overrides, int) and max_overrides >= 0:
            used = self._near_threshold_usage.get(tier_key, 0)
            if used >= max_overrides:
                logger.debug(
                    "%s near-threshold override skipped: tier %s cap reached (%d/%d)",
                    symbol,
                    tier_key,
                    used,
                    max_overrides,
                )
                return None
            self._near_threshold_usage[tier_key] = used + 1
        else:
            self._near_threshold_usage[tier_key] = self._near_threshold_usage.get(tier_key, 0) + 1

        logger.info(
            "OVERRIDE: %s included via near-threshold (%s=$%.0f floor=$%.0f tol=%.0f%% tier=%d)",
            symbol,
            metric,
            metric_value,
            floor,
            tolerance_pct * 100.0,
            tier,
        )
        return reason_code

    def _check_liquidity(self, quote: Quote, orderbook, 
                         global_config: dict, tier_config: dict, tier: int = 3) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Check if asset passes liquidity requirements.

        Args:
            quote: Price quote
            orderbook: Order book data
            global_config: Global liquidity config
            tier_config: Tier-specific config
            tier: Tier number (1, 2, or 3) for depth requirements

        Returns:
            Tuple[eligible_flag, ineligible_reason, eligibility_reason]
        """
        eligibility_reason: Optional[str] = None

        # Force-eligible override: bypass all liquidity checks for core assets
        force_eligible = tier_config.get("force_eligible_symbols", [])
        if quote.symbol in force_eligible:
            logger.info(f"✅ FORCE ELIGIBLE: {quote.symbol} bypasses liquidity checks (core asset)")
            return True, None, "force_eligible_core_asset"

        # Volume check with tier and global near-threshold overrides
        min_volume_global = global_config.get("min_24h_volume_usd", 5_000_000)
        min_volume_tier = tier_config.get("min_24h_volume_usd", min_volume_global)
        min_volume = max(min_volume_global, min_volume_tier)

        # Check if in override zone (95% of floor → floor)
        # Note: tier_config is actually the constraints dict when called from _build_tier_2
        override_config = tier_config.get("near_threshold_override", {})
        override_enabled = override_config.get("enable", False)
        lower_mult = override_config.get("lower_mult", 0.95)
        override_floor = min_volume * lower_mult

        # Diagnostic logging for near-threshold assets
        if quote.volume_24h < min_volume and quote.volume_24h >= override_floor * 0.9:
            logger.info(
                f"DIAGNOSTIC: {quote.symbol} near threshold - "
                f"volume=${quote.volume_24h:,.0f}, floor=${min_volume:,.0f}, "
                f"override_floor=${override_floor:,.0f}, override_enabled={override_enabled}, tier={tier}"
            )

        if quote.volume_24h < min_volume:
            override_reason = None
            if override_enabled and tier == 2 and quote.volume_24h >= override_floor:
                logger.info(
                    "OVERRIDE CHECK: %s in zone - volume=$%s ($%s-$%s), spread=%.1fbps",
                    quote.symbol,
                    f"{quote.volume_24h:,.0f}",
                    f"{override_floor:,.0f}",
                    f"{min_volume:,.0f}",
                    quote.spread_bps,
                )

                override_max_spread = override_config.get("max_spread_bps", 30)
                if quote.spread_bps > override_max_spread:
                    logger.warning(
                        "OVERRIDE REJECT: %s spread %.1fbps > %.1fbps",
                        quote.symbol,
                        quote.spread_bps,
                        override_max_spread,
                    )
                    return False, (
                        f"Volume ${quote.volume_24h:,.0f} in override zone but spread {quote.spread_bps:.1f}bps > {override_max_spread}bps"
                    ), None

                override_reason = "override_volume"
                logger.info(
                    "✅ OVERRIDE PASS: %s volume $%s ($%s–$%s), spread %.1fbps ≤ %.1fbps - ALLOWED",
                    quote.symbol,
                    f"{quote.volume_24h:,.0f}",
                    f"{override_floor:,.0f}",
                    f"{min_volume:,.0f}",
                    quote.spread_bps,
                    override_max_spread,
                )
            else:
                applied_reason = self._apply_near_threshold_override(
                    symbol=quote.symbol,
                    tier=tier,
                    metric="volume",
                    metric_value=quote.volume_24h,
                    floor=min_volume,
                    reason_code="override_volume",
                )
                if applied_reason:
                    override_reason = applied_reason

            if override_reason:
                eligibility_reason = override_reason
            else:
                if not override_enabled:
                    logger.debug(f"{quote.symbol}: override disabled")
                elif tier != 2:
                    logger.debug(f"{quote.symbol}: wrong tier (T{tier}, need T2)")
                elif quote.volume_24h < override_floor:
                    logger.debug(
                        f"{quote.symbol}: below override floor "
                        f"(${quote.volume_24h:,.0f} < ${override_floor:,.0f})"
                    )
                else:
                    logger.debug(f"{quote.symbol}: override conditions not met")
                return False, f"Volume ${quote.volume_24h:,.0f} < ${min_volume:,.0f}", None

        # Spread check
        max_spread_global = global_config.get("max_spread_bps", 100)
        max_spread_tier = tier_config.get("max_spread_bps", max_spread_global)
        max_spread = min(max_spread_global, max_spread_tier)

        if quote.spread_bps > max_spread:
            logger.debug(
                f"{quote.symbol}: spread check FAIL - "
                f"{quote.spread_bps:.1f}bps > {max_spread}bps (T{tier})"
            )
            return False, f"Spread {quote.spread_bps:.1f}bps > {max_spread}bps", None

        logger.debug(f"{quote.symbol}: spread check PASS - {quote.spread_bps:.1f}bps ≤ {max_spread}bps")

        # Depth check (tier-specific)
        # Try tier-specific depth first, fallback to legacy global
        depth_key = f"min_orderbook_depth_usd_t{tier}"
        min_depth_tier = global_config.get(depth_key)
        if min_depth_tier is None:
            # Fallback to legacy global setting
            min_depth_tier = global_config.get("min_orderbook_depth_usd", 10_000)

        if orderbook.total_depth_usd < min_depth_tier:
            applied_depth_override = self._apply_near_threshold_override(
                symbol=quote.symbol,
                tier=tier,
                metric="depth",
                metric_value=orderbook.total_depth_usd,
                floor=min_depth_tier,
                reason_code="override_depth",
            )

            if applied_depth_override:
                eligibility_reason = eligibility_reason or applied_depth_override
            else:
                logger.debug(
                    f"{quote.symbol}: depth check FAIL - "
                    f"${orderbook.total_depth_usd:,.0f} < ${min_depth_tier:,.0f} (T{tier})"
                )
                return False, f"Depth ${orderbook.total_depth_usd:,.0f} < ${min_depth_tier:,.0f} (T{tier})", None

        logger.debug(
            f"{quote.symbol}: depth check PASS - "
            f"${orderbook.total_depth_usd:,.0f} ≥ ${min_depth_tier:,.0f} (T{tier})"
        )

        if eligibility_reason:
            logger.info(
                "✅ ELIGIBLE: %s passed all T%d liquidity checks (reason=%s)",
                quote.symbol,
                tier,
                eligibility_reason,
            )
        else:
            logger.info(f"✅ ELIGIBLE: {quote.symbol} passed all T{tier} liquidity checks")

        return True, None, eligibility_reason

    def _is_cache_valid(self, regime: str) -> bool:
        """Check if cached snapshot is still valid"""
        if self._cache is None or self._cache_time is None:
            return False

        # Check regime matches
        if self._cache.regime != regime:
            return False

        # Check age
        cache_time = self._cache_time
        if cache_time.tzinfo is None:
            cache_time = cache_time.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - cache_time
        return age < self._cache_ttl

    def _get_cached_products(self) -> Optional[List[str]]:
        """
        Get cached product list if valid, else None.

        OPTIMIZATION: Avoid redundant list_products() calls (80-100% rate limit warnings).
        Products rarely change intraday, safe to cache for 5+ minutes.
        """
        if self._products_cache is None or self._products_cache_time is None:
            return None

        cache_time = self._products_cache_time
        if cache_time.tzinfo is None:
            cache_time = cache_time.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - cache_time
        if age < self._products_cache_ttl:
            logger.debug(f"Products cache HIT (age: {age.total_seconds():.1f}s / {self._products_cache_ttl.total_seconds()}s)")
            return self._products_cache
        else:
            logger.debug(f"Products cache MISS (stale: {age.total_seconds():.1f}s > {self._products_cache_ttl.total_seconds()}s)")
            return None

    def _update_products_cache(self, symbols: List[str]) -> None:
        """Update products cache with fresh data"""
        self._products_cache = symbols
        self._products_cache_time = datetime.now(timezone.utc)
        logger.debug(f"Products cache updated: {len(symbols)} symbols, expires in {self._products_cache_ttl.total_seconds()}s")

    def get_cluster_limits(self) -> Dict[str, float]:
        """Get cluster exposure limits"""
        return self.config.get("clusters", {}).get("limits", {})

    def get_asset_cluster(self, symbol: str) -> Optional[str]:
        """Get cluster membership for asset"""
        clusters = self.config.get("clusters", {}).get("definitions", {})
        for cluster_name, symbols in clusters.items():
            if symbol in symbols:
                return cluster_name
        return None
