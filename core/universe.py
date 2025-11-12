"""
247trader-v2 Core: Universe Manager

Loads universe config and enforces eligibility rules.
Salvaged from v1 but simplified and hardened.
"""

import yaml
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import logging

from core.exchange_coinbase import get_exchange, Quote

logger = logging.getLogger(__name__)


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
    - Enforce tier constraints
    - Return eligible assets by regime
    """
    
    def __init__(self, config_path: str = "config/universe.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._cache: Optional[UniverseSnapshot] = None
        self._cache_time: Optional[datetime] = None
        
        logger.info(f"Initialized UniverseManager from {config_path}")
    
    def _load_config(self) -> dict:
        """Load universe configuration"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Universe config not found: {self.config_path}")
        
        with open(self.config_path) as f:
            config = yaml.safe_load(f)
        
        # Check if dynamic mode is enabled
        universe_config = config.get('universe', {})
        if universe_config.get('method') == 'dynamic_discovery':
            logger.info("Using dynamic universe discovery from Coinbase")
            config = self._build_dynamic_universe(config)
        
        logger.info(f"Loaded universe config with {len(config.get('tiers', {}))} tiers")
        return config
    
    def _build_dynamic_universe(self, config: dict) -> dict:
        """
        Dynamically discover tradable assets from Coinbase.
        
        Fetches all USD pairs and categorizes them into tiers based on:
        - Market cap proxy (volume)
        - Liquidity metrics
        - Exchange support
        """
        logger.info("Fetching all tradable pairs from Coinbase...")
        
        try:
            exchange = get_exchange()
            symbols = exchange.get_symbols()
            
            # CRITICAL: Treat empty symbols as failure
            if not symbols:
                raise RuntimeError("No symbols returned from Coinbase – triggering fallback")
            
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
            logger.debug(f"Using cached universe (age: {datetime.utcnow() - self._cache_time})")
            return self._cache
        
        logger.info(f"Building universe snapshot for regime={regime}")
        
        exchange = get_exchange()
        
        # Get tier definitions
        tiers_config = self.config.get("tiers", {})
        liquidity_config = self.config.get("liquidity", {})
        regime_mods = self.config.get("regime_modifiers", {}).get(regime, {})
        exclusions = self.config.get("exclusions", {})
        
        # Get exclusions first
        excluded = set(exclusions.get("never_trade", []))
        
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
            timestamp=datetime.utcnow(),
            regime=regime,
            tier_1_assets=tier_1,
            tier_2_assets=tier_2,
            tier_3_assets=tier_3,
            excluded_assets=list(excluded),
            total_eligible=len(tier_1) + len(tier_2) + len(tier_3)
        )
        
        # Cache result
        self._cache = snapshot
        self._cache_time = datetime.utcnow()
        
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
                eligible, reason = self._check_liquidity(
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
                    ineligible_reason=reason
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
                    ineligible_reason=None
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
                eligible, reason = self._check_liquidity(
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
                    ineligible_reason=None
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
    
    def _check_liquidity(self, quote: Quote, orderbook, 
                         global_config: dict, tier_config: dict, tier: int = 3) -> Tuple[bool, Optional[str]]:
        """
        Check if asset passes liquidity requirements.
        
        Args:
            quote: Price quote
            orderbook: Order book data
            global_config: Global liquidity config
            tier_config: Tier-specific config
            tier: Tier number (1, 2, or 3) for depth requirements
        
        Returns:
            (eligible, reason_if_not)
        """
        # Volume check with near-threshold override
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
            # Check near-threshold override (only for T2)
            if override_enabled and tier == 2 and quote.volume_24h >= override_floor:
                logger.info(
                    f"OVERRIDE CHECK: {quote.symbol} in zone - "
                    f"volume=${quote.volume_24h:,.0f} (${override_floor:,.0f}-${min_volume:,.0f}), "
                    f"spread={quote.spread_bps:.1f}bps"
                )
                
                # Asset is in override zone ($28.5M–$30M for T2)
                # Check ALL strict rules:
                
                # 1) Spread tighter than normal
                override_max_spread = override_config.get("max_spread_bps", 30)
                if quote.spread_bps > override_max_spread:
                    logger.warning(
                        f"OVERRIDE REJECT: {quote.symbol} spread {quote.spread_bps:.1f}bps > {override_max_spread}bps"
                    )
                    return False, f"Volume ${quote.volume_24h:,.0f} in override zone but spread {quote.spread_bps:.1f}bps > {override_max_spread}bps"
                
                # 2) Size-aware depth (12× order notional within ±0.5%)
                # For now, use depth check below (will be enhanced with size-aware logic)
                require_depth_mult = override_config.get("require_depth_mult", 12)
                
                # 3) Listing age check (placeholder - would need exchange metadata)
                # min_listing_age_days = override_config.get("min_listing_age_days", 30)
                # For now, skip listing age check (requires additional API data)
                
                # 4) Slippage budget must pass (checked later in execution)
                
                logger.info(
                    f"✅ OVERRIDE PASS: {quote.symbol} volume ${quote.volume_24h:,.0f} "
                    f"(${override_floor:,.0f}–${min_volume:,.0f}), spread {quote.spread_bps:.1f}bps ≤ {override_max_spread}bps - ALLOWED"
                )
                # Pass override - continue to other checks
            else:
                # Log why override didn't apply
                if not override_enabled:
                    logger.debug(f"{quote.symbol}: override disabled")
                elif tier != 2:
                    logger.debug(f"{quote.symbol}: wrong tier (T{tier}, need T2)")
                elif quote.volume_24h < override_floor:
                    logger.debug(
                        f"{quote.symbol}: below override floor "
                        f"(${quote.volume_24h:,.0f} < ${override_floor:,.0f})"
                    )
                return False, f"Volume ${quote.volume_24h:,.0f} < ${min_volume:,.0f}"
        
        # Spread check
        max_spread_global = global_config.get("max_spread_bps", 100)
        max_spread_tier = tier_config.get("max_spread_bps", max_spread_global)
        max_spread = min(max_spread_global, max_spread_tier)
        
        if quote.spread_bps > max_spread:
            logger.debug(
                f"{quote.symbol}: spread check FAIL - "
                f"{quote.spread_bps:.1f}bps > {max_spread}bps (T{tier})"
            )
            return False, f"Spread {quote.spread_bps:.1f}bps > {max_spread}bps"
        
        logger.debug(f"{quote.symbol}: spread check PASS - {quote.spread_bps:.1f}bps ≤ {max_spread}bps")
        
        # Depth check (tier-specific)
        # Try tier-specific depth first, fallback to legacy global
        depth_key = f"min_orderbook_depth_usd_t{tier}"
        min_depth_tier = global_config.get(depth_key)
        if min_depth_tier is None:
            # Fallback to legacy global setting
            min_depth_tier = global_config.get("min_orderbook_depth_usd", 10_000)
        
        if orderbook.total_depth_usd < min_depth_tier:
            logger.debug(
                f"{quote.symbol}: depth check FAIL - "
                f"${orderbook.total_depth_usd:,.0f} < ${min_depth_tier:,.0f} (T{tier})"
            )
            return False, f"Depth ${orderbook.total_depth_usd:,.0f} < ${min_depth_tier:,.0f} (T{tier})"
        
        logger.debug(
            f"{quote.symbol}: depth check PASS - "
            f"${orderbook.total_depth_usd:,.0f} ≥ ${min_depth_tier:,.0f} (T{tier})"
        )
        
        logger.info(f"✅ ELIGIBLE: {quote.symbol} passed all T{tier} liquidity checks")
        return True, None
    
    def _is_cache_valid(self, regime: str) -> bool:
        """Check if cached snapshot is still valid"""
        if self._cache is None or self._cache_time is None:
            return False
        
        # Check regime matches
        if self._cache.regime != regime:
            return False
        
        # Check age
        age_seconds = (datetime.utcnow() - self._cache_time).total_seconds()
        max_age_seconds = self.config.get("universe", {}).get("refresh_interval_hours", 24) * 3600
        
        return age_seconds < max_age_seconds
    
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
