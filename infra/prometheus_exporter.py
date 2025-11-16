"""
Prometheus metrics exporter for 247trader-v2

Exposes trading metrics in Prometheus format for Grafana visualization.
"""

from prometheus_client import Counter, Gauge, Histogram, start_http_server, CollectorRegistry, REGISTRY
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PrometheusExporter:
    """Export trading metrics to Prometheus"""
    
    def __init__(self, port: int = 8000, registry: Optional[CollectorRegistry] = None):
        self.port = port
        self.registry = registry or REGISTRY
        
        # Try to create metrics, catch duplicates (happens in tests)
        try:
            # Trading metrics
            self.trades_total = Counter('trader_trades_total', 'Total number of trades', ['side', 'symbol'], registry=self.registry)
            self.trade_pnl = Gauge('trader_trade_pnl_usd', 'Trade PnL in USD', ['symbol'], registry=self.registry)
            self.daily_pnl = Gauge('trader_daily_pnl_usd', 'Daily PnL in USD', registry=self.registry)
            self.daily_pnl_pct = Gauge('trader_daily_pnl_pct', 'Daily PnL percentage', registry=self.registry)
            self.account_value = Gauge('trader_account_value_usd', 'Total account value in USD', registry=self.registry)
            
            # Position metrics
            self.open_positions = Gauge('trader_open_positions', 'Number of open positions', registry=self.registry)
            self.position_value = Gauge('trader_position_value_usd', 'Position value in USD', ['symbol'], registry=self.registry)
            self.exposure_pct = Gauge('trader_exposure_pct', 'Total exposure as % of account', registry=self.registry)
            
            # Risk metrics
            self.max_drawdown = Gauge('trader_max_drawdown_pct', 'Maximum drawdown percentage', registry=self.registry)
            self.risk_rejections = Counter('trader_risk_rejections_total', 'Risk check rejections', ['reason'], registry=self.registry)
            self.circuit_breaker_trips = Counter('trader_circuit_breaker_trips', 'Circuit breaker activations', ['type'], registry=self.registry)
            
            # System metrics
            self.cycle_duration = Histogram('trader_cycle_duration_seconds', 'Trading loop cycle duration', registry=self.registry)
            self.api_latency = Histogram('trader_api_latency_seconds', 'Exchange API latency', ['endpoint'], registry=self.registry)
            self.api_errors = Counter('trader_api_errors_total', 'API error count', ['endpoint', 'error_type'], registry=self.registry)
            self.data_staleness = Gauge('trader_data_staleness_seconds', 'Data staleness in seconds', ['data_type'], registry=self.registry)
            
            # Execution metrics
            self.orders_placed = Counter('trader_orders_placed_total', 'Orders placed', ['side', 'symbol'], registry=self.registry)
            self.orders_filled = Counter('trader_orders_filled_total', 'Orders filled', ['side', 'symbol'], registry=self.registry)
            self.orders_canceled = Counter('trader_orders_canceled_total', 'Orders canceled', ['symbol'], registry=self.registry)
            self.order_fill_time = Histogram('trader_order_fill_time_seconds', 'Time to fill order', registry=self.registry)
            
            self._metrics_initialized = True
        except ValueError as e:
            if "Duplicated" in str(e):
                logger.debug(f"Prometheus metrics already registered (expected in tests): {e}")
                self._metrics_initialized = False
            else:
                raise
        
    def start(self):
        """Start Prometheus HTTP server"""
        if not self._metrics_initialized:
            logger.debug("Skipping Prometheus HTTP server start (metrics not initialized)")
            return
        try:
            start_http_server(self.port, registry=self.registry)
            logger.info(f"Prometheus exporter started on port {self.port}")
        except Exception as e:
            logger.error(f"Failed to start Prometheus exporter: {e}")
    
    def update_from_cycle_stats(self, stats: dict):
        """Update metrics from cycle statistics"""
        try:
            # Account metrics
            if 'account_value_usd' in stats:
                self.account_value.set(stats['account_value_usd'])
            
            if 'daily_pnl_usd' in stats:
                self.daily_pnl.set(stats['daily_pnl_usd'])
            
            if 'daily_pnl_pct' in stats:
                self.daily_pnl_pct.set(stats['daily_pnl_pct'])
            
            if 'max_drawdown_pct' in stats:
                self.max_drawdown.set(stats['max_drawdown_pct'])
            
            # Position metrics
            if 'open_positions' in stats:
                self.open_positions.set(len(stats['open_positions']))
            
            if 'exposure_pct' in stats:
                self.exposure_pct.set(stats['exposure_pct'])
            
            # Cycle timing
            if 'cycle_duration_seconds' in stats:
                self.cycle_duration.observe(stats['cycle_duration_seconds'])
                
        except Exception as e:
            logger.warning(f"Error updating Prometheus metrics: {e}")
    
    def record_trade(self, symbol: str, side: str, pnl_usd: Optional[float] = None):
        """Record a trade execution"""
        self.trades_total.labels(side=side.upper(), symbol=symbol).inc()
        if pnl_usd is not None:
            self.trade_pnl.labels(symbol=symbol).set(pnl_usd)
    
    def record_order_placed(self, symbol: str, side: str):
        """Record order placement"""
        self.orders_placed.labels(side=side.upper(), symbol=symbol).inc()
    
    def record_order_filled(self, symbol: str, side: str, fill_time_seconds: float):
        """Record order fill"""
        self.orders_filled.labels(side=side.upper(), symbol=symbol).inc()
        self.order_fill_time.observe(fill_time_seconds)
    
    def record_risk_rejection(self, reason: str):
        """Record risk check rejection"""
        self.risk_rejections.labels(reason=reason).inc()
    
    def record_circuit_breaker(self, breaker_type: str):
        """Record circuit breaker activation"""
        self.circuit_breaker_trips.labels(type=breaker_type).inc()
    
    def record_api_latency(self, endpoint: str, latency_seconds: float):
        """Record API call latency"""
        self.api_latency.labels(endpoint=endpoint).observe(latency_seconds)
    
    def record_api_error(self, endpoint: str, error_type: str):
        """Record API error"""
        self.api_errors.labels(endpoint=endpoint, error_type=error_type).inc()
    
    def set_data_staleness(self, data_type: str, age_seconds: float):
        """Update data staleness metric"""
        self.data_staleness.labels(data_type=data_type).set(age_seconds)


# Singleton instance
_exporter: Optional[PrometheusExporter] = None


def get_exporter(port: int = 8000) -> PrometheusExporter:
    """
    Get or create Prometheus exporter singleton.
    
    Returns None if called before initialization (allows optional Prometheus).
    Main loop should call this with explicit port to initialize, 
    other components can call without args to get existing instance.
    """
    global _exporter
    if _exporter is None:
        _exporter = PrometheusExporter(port=port)
    return _exporter
