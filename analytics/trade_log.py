"""
247trader-v2 Analytics: Trade Log with PnL Attribution

Persistent trade records with detailed PnL decomposition.
Enables backtest vs live comparison and per-signal performance analysis.

PnL Attribution:
- Edge PnL: Price improvement (entry/exit vs mid-market)
- Fees: Maker/taker fees
- Slippage: Adverse price movement during execution

Schema supports:
- Per-trade metrics (win/loss, duration, drawdown)
- Per-signal performance (which signals are profitable)
- Per-symbol/regime/tier analysis
- Backtest validation (compare predicted vs actual PnL)
"""

import csv
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """
    Complete trade record with PnL attribution.
    
    Lifecycle:
    1. Entry: Create record with entry data
    2. Exit: Update with exit data and calculate PnL
    3. Attribution: Decompose PnL into edge/fees/slippage
    """
    # Identity
    trade_id: str  # Unique identifier
    symbol: str
    side: str  # BUY/SELL
    
    # Timing
    entry_time: datetime
    exit_time: Optional[datetime] = None
    hold_duration_minutes: Optional[float] = None
    
    # Sizing
    size_quote: float  # Quote currency amount (e.g., USD)
    size_base: Optional[float] = None  # Base currency amount (e.g., BTC)
    
    # Pricing
    entry_price: float
    exit_price: Optional[float] = None
    entry_mid_price: Optional[float] = None  # Mid-market at entry
    exit_mid_price: Optional[float] = None  # Mid-market at exit
    
    # PnL (all in quote currency)
    pnl_gross: Optional[float] = None  # Exit value - entry value
    pnl_net: Optional[float] = None  # After all costs
    pnl_pct: Optional[float] = None  # Return %
    
    # PnL Attribution
    edge_pnl: Optional[float] = None  # Price improvement vs mid
    fees_total: Optional[float] = None  # All fees
    slippage: Optional[float] = None  # Adverse price movement
    
    # Fee Breakdown
    entry_fee: Optional[float] = None
    exit_fee: Optional[float] = None
    entry_is_maker: bool = False
    exit_is_maker: bool = False
    
    # Strategy Context
    trigger_type: Optional[str] = None  # Signal that triggered trade
    trigger_confidence: Optional[float] = None
    rule_name: Optional[str] = None  # Rule that proposed trade
    conviction: Optional[float] = None  # Final conviction score
    regime: Optional[str] = None  # Market regime at entry
    
    # Risk Parameters
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    max_hold_hours: Optional[int] = None
    
    # Execution
    exit_reason: Optional[str] = None  # stop_loss/take_profit/max_hold/manual
    hit_stop_loss: bool = False
    hit_take_profit: bool = False
    
    # Portfolio Context
    nav_before: Optional[float] = None  # NAV before entry
    nav_after: Optional[float] = None  # NAV after exit
    nav_drawdown_pct: Optional[float] = None  # Max drawdown during trade
    
    # Metadata
    tags: List[str] = None
    notes: Optional[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
    
    def calculate_pnl(self):
        """Calculate PnL metrics"""
        if self.exit_price is None:
            return
        
        # Gross PnL (ignoring fees)
        if self.side == "BUY":
            self.pnl_gross = (self.exit_price - self.entry_price) * (self.size_quote / self.entry_price)
        else:  # SELL
            self.pnl_gross = (self.entry_price - self.exit_price) * (self.size_quote / self.entry_price)
        
        # Net PnL (after fees)
        self.pnl_net = self.pnl_gross - (self.fees_total or 0)
        
        # Return %
        self.pnl_pct = (self.pnl_net / self.size_quote) * 100 if self.size_quote > 0 else 0
        
        # Hold duration
        if self.exit_time:
            self.hold_duration_minutes = (self.exit_time - self.entry_time).total_seconds() / 60
    
    def calculate_attribution(self):
        """Decompose PnL into edge, fees, slippage"""
        if self.exit_price is None or self.entry_mid_price is None:
            return
        
        # Edge: Price improvement vs mid-market
        if self.side == "BUY":
            entry_edge = (self.entry_mid_price - self.entry_price) * (self.size_quote / self.entry_price)
            exit_edge = (self.exit_price - (self.exit_mid_price or self.exit_price)) * (self.size_quote / self.entry_price)
        else:  # SELL
            entry_edge = (self.entry_price - self.entry_mid_price) * (self.size_quote / self.entry_price)
            exit_edge = ((self.exit_mid_price or self.exit_price) - self.exit_price) * (self.size_quote / self.entry_price)
        
        self.edge_pnl = entry_edge + exit_edge
        
        # Fees
        self.fees_total = (self.entry_fee or 0) + (self.exit_fee or 0)
        
        # Slippage (residual: gross PnL - edge)
        self.slippage = self.pnl_gross - self.edge_pnl if self.pnl_gross and self.edge_pnl else 0
    
    def to_dict(self) -> Dict:
        """Convert to dictionary (for CSV/JSON export)"""
        d = asdict(self)
        # Convert datetime to ISO format
        if isinstance(d['entry_time'], datetime):
            d['entry_time'] = d['entry_time'].isoformat()
        if d['exit_time'] and isinstance(d['exit_time'], datetime):
            d['exit_time'] = d['exit_time'].isoformat()
        return d


class TradeLog:
    """
    Persistent trade log with multiple backends.
    
    Backends:
    - CSV: Simple, portable, Excel-compatible
    - SQLite: Queryable, good for analysis
    - JSON: Flexible, easy debugging
    
    Default: CSV + SQLite for best of both worlds
    """
    
    def __init__(
        self,
        log_dir: str = "data/trades",
        backend: str = "csv",  # csv, sqlite, json
        enable_sqlite: bool = True
    ):
        """
        Initialize trade log.
        
        Args:
            log_dir: Directory for trade logs
            backend: Primary backend (csv, sqlite, json)
            enable_sqlite: Also maintain SQLite for queries
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.backend = backend
        self.enable_sqlite = enable_sqlite
        
        # File paths
        self.csv_file = self.log_dir / "trades.csv"
        self.json_file = self.log_dir / "trades.jsonl"  # JSON Lines format
        self.db_file = self.log_dir / "trades.db"
        
        # Initialize backends
        if backend == "csv" or enable_sqlite:
            self._init_csv()
        if backend == "sqlite" or enable_sqlite:
            self._init_sqlite()
        
        logger.info(f"TradeLog initialized: backend={backend}, dir={log_dir}, sqlite={enable_sqlite}")
    
    def _init_csv(self):
        """Initialize CSV file with headers"""
        if not self.csv_file.exists():
            with open(self.csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'trade_id', 'symbol', 'side', 'entry_time', 'exit_time',
                    'size_quote', 'entry_price', 'exit_price',
                    'pnl_gross', 'pnl_net', 'pnl_pct',
                    'edge_pnl', 'fees_total', 'slippage',
                    'entry_fee', 'exit_fee', 'entry_is_maker', 'exit_is_maker',
                    'trigger_type', 'conviction', 'regime',
                    'stop_loss_pct', 'take_profit_pct', 'exit_reason',
                    'hold_duration_minutes', 'nav_before', 'nav_after',
                    'tags', 'notes'
                ])
    
    def _init_sqlite(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(str(self.db_file))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_time TIMESTAMP NOT NULL,
                exit_time TIMESTAMP,
                size_quote REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                pnl_gross REAL,
                pnl_net REAL,
                pnl_pct REAL,
                edge_pnl REAL,
                fees_total REAL,
                slippage REAL,
                entry_fee REAL,
                exit_fee REAL,
                entry_is_maker INTEGER,
                exit_is_maker INTEGER,
                trigger_type TEXT,
                trigger_confidence REAL,
                conviction REAL,
                regime TEXT,
                stop_loss_pct REAL,
                take_profit_pct REAL,
                exit_reason TEXT,
                hold_duration_minutes REAL,
                nav_before REAL,
                nav_after REAL,
                tags TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Indexes for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON trades(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entry_time ON trades(entry_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trigger_type ON trades(trigger_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_regime ON trades(regime)")
        
        conn.commit()
        conn.close()
    
    def log_entry(self, trade: TradeRecord):
        """Log trade entry"""
        if self.backend == "csv":
            self._append_csv(trade)
        elif self.backend == "json":
            self._append_json(trade)
        
        if self.enable_sqlite:
            self._insert_sqlite(trade)
        
        logger.info(f"Logged entry: {trade.trade_id} ({trade.symbol}, {trade.side})")
    
    def log_exit(self, trade: TradeRecord):
        """Log trade exit and update record"""
        # Calculate PnL and attribution
        trade.calculate_pnl()
        trade.calculate_attribution()
        
        if self.backend == "sqlite" or self.enable_sqlite:
            self._update_sqlite(trade)
        
        # For CSV/JSON, we append a new "exit" record
        if self.backend == "csv":
            self._append_csv(trade)
        elif self.backend == "json":
            self._append_json(trade)
        
        logger.info(
            f"Logged exit: {trade.trade_id} ({trade.symbol}, "
            f"PnL={trade.pnl_net:.2f}, return={trade.pnl_pct:.2f}%)"
        )
    
    def _append_csv(self, trade: TradeRecord):
        """Append trade to CSV"""
        with open(self.csv_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                trade.trade_id, trade.symbol, trade.side,
                trade.entry_time.isoformat() if trade.entry_time else '',
                trade.exit_time.isoformat() if trade.exit_time else '',
                trade.size_quote, trade.entry_price, trade.exit_price,
                trade.pnl_gross, trade.pnl_net, trade.pnl_pct,
                trade.edge_pnl, trade.fees_total, trade.slippage,
                trade.entry_fee, trade.exit_fee, trade.entry_is_maker, trade.exit_is_maker,
                trade.trigger_type, trade.conviction, trade.regime,
                trade.stop_loss_pct, trade.take_profit_pct, trade.exit_reason,
                trade.hold_duration_minutes, trade.nav_before, trade.nav_after,
                json.dumps(trade.tags), trade.notes
            ])
    
    def _append_json(self, trade: TradeRecord):
        """Append trade to JSONL file"""
        with open(self.json_file, 'a') as f:
            f.write(json.dumps(trade.to_dict()) + '\n')
    
    def _insert_sqlite(self, trade: TradeRecord):
        """Insert trade into SQLite"""
        conn = sqlite3.connect(str(self.db_file))
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO trades VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            trade.trade_id, trade.symbol, trade.side,
            trade.entry_time.isoformat() if trade.entry_time else None,
            trade.exit_time.isoformat() if trade.exit_time else None,
            trade.size_quote, trade.entry_price, trade.exit_price,
            trade.pnl_gross, trade.pnl_net, trade.pnl_pct,
            trade.edge_pnl, trade.fees_total, trade.slippage,
            trade.entry_fee, trade.exit_fee,
            1 if trade.entry_is_maker else 0,
            1 if trade.exit_is_maker else 0,
            trade.trigger_type, trade.trigger_confidence, trade.conviction,
            trade.regime, trade.stop_loss_pct, trade.take_profit_pct,
            trade.exit_reason, trade.hold_duration_minutes,
            trade.nav_before, trade.nav_after,
            json.dumps(trade.tags), trade.notes,
            datetime.now(timezone.utc).isoformat()
        ))
        
        conn.commit()
        conn.close()
    
    def _update_sqlite(self, trade: TradeRecord):
        """Update existing trade in SQLite"""
        conn = sqlite3.connect(str(self.db_file))
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE trades SET
                exit_time = ?,
                exit_price = ?,
                pnl_gross = ?,
                pnl_net = ?,
                pnl_pct = ?,
                edge_pnl = ?,
                fees_total = ?,
                slippage = ?,
                exit_fee = ?,
                exit_is_maker = ?,
                exit_reason = ?,
                hold_duration_minutes = ?,
                nav_after = ?
            WHERE trade_id = ?
        """, (
            trade.exit_time.isoformat() if trade.exit_time else None,
            trade.exit_price,
            trade.pnl_gross, trade.pnl_net, trade.pnl_pct,
            trade.edge_pnl, trade.fees_total, trade.slippage,
            trade.exit_fee,
            1 if trade.exit_is_maker else 0,
            trade.exit_reason,
            trade.hold_duration_minutes,
            trade.nav_after,
            trade.trade_id
        ))
        
        conn.commit()
        conn.close()
    
    def query(self, sql: str) -> List[Dict]:
        """
        Execute SQL query on trade database.
        
        Args:
            sql: SQL query string
            
        Returns:
            List of result rows as dictionaries
        """
        if not self.enable_sqlite:
            raise ValueError("SQLite not enabled for this log")
        
        conn = sqlite3.connect(str(self.db_file))
        conn.row_factory = sqlite3.Row  # Return rows as dicts
        cursor = conn.cursor()
        
        cursor.execute(sql)
        results = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return results
    
    def get_recent_trades(self, limit: int = 100) -> List[TradeRecord]:
        """Get most recent trades"""
        if not self.enable_sqlite:
            return []
        
        results = self.query(f"""
            SELECT * FROM trades
            ORDER BY entry_time DESC
            LIMIT {limit}
        """)
        
        # Convert back to TradeRecord objects
        trades = []
        for row in results:
            # Parse datetimes
            if row['entry_time']:
                row['entry_time'] = datetime.fromisoformat(row['entry_time'])
            if row['exit_time']:
                row['exit_time'] = datetime.fromisoformat(row['exit_time'])
            
            # Parse tags
            if row['tags']:
                row['tags'] = json.loads(row['tags'])
            
            # Convert boolean integers
            row['entry_is_maker'] = bool(row['entry_is_maker'])
            row['exit_is_maker'] = bool(row['exit_is_maker'])
            
            # Remove SQLite-specific fields
            row.pop('created_at', None)
            
            trades.append(TradeRecord(**row))
        
        return trades
    
    def get_summary_stats(self) -> Dict:
        """Get summary statistics"""
        if not self.enable_sqlite:
            return {}
        
        results = self.query("""
            SELECT
                COUNT(*) as total_trades,
                COUNT(CASE WHEN exit_time IS NOT NULL THEN 1 END) as closed_trades,
                SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_net < 0 THEN 1 ELSE 0 END) as losses,
                SUM(pnl_net) as total_pnl,
                AVG(pnl_net) as avg_pnl,
                AVG(pnl_pct) as avg_return_pct,
                AVG(hold_duration_minutes) as avg_hold_minutes,
                SUM(fees_total) as total_fees,
                SUM(edge_pnl) as total_edge
            FROM trades
            WHERE exit_time IS NOT NULL
        """)
        
        if results:
            return results[0]
        return {}
