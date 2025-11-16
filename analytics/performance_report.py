"""
247trader-v2 Analytics: Performance Metrics & Reports

Comprehensive performance analysis for trading strategies.
Supports both backtest and live validation.

Metrics Hierarchy:
1. Returns: PnL, win rate, avg win/loss, profit factor
2. Risk: Sharpe, max DD, volatility, MAE/MFE
3. Execution: Maker ratio, slippage, fee impact
4. Strategy: Per-signal/regime/symbol performance
5. Behavioral: Hold time, trade frequency, hit rates

Outputs:
- JSON reports (machine-readable)
- Markdown summaries (human-readable)
- CSV exports (spreadsheet analysis)
"""

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging
import math

from analytics.trade_log import TradeLog, TradeRecord

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics"""
    
    # Period
    start_date: datetime
    end_date: datetime
    days: float
    
    # Returns
    total_trades: int
    closed_trades: int
    open_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    
    pnl_total: float
    pnl_avg: float
    pnl_median: float
    pnl_std: float
    
    return_pct_total: float
    return_pct_avg: float
    return_pct_annualized: float
    
    avg_win: float
    avg_loss: float
    profit_factor: float  # Avg win / avg loss
    
    largest_win: float
    largest_loss: float
    
    # Risk
    sharpe_ratio: float  # Risk-adjusted return
    sortino_ratio: float  # Downside risk-adjusted
    max_drawdown_pct: float
    max_drawdown_duration_days: float
    
    volatility_daily_pct: float
    volatility_annual_pct: float
    
    # Risk of Ruin
    consecutive_losses_max: int
    consecutive_wins_max: int
    
    # Trade Efficiency
    mae_avg: float  # Max Adverse Excursion
    mfe_avg: float  # Max Favorable Excursion
    
    # Execution Quality
    total_fees: float
    fee_impact_pct: float  # Fees / gross PnL
    
    edge_pnl_total: float
    edge_capture_pct: float  # Edge / gross PnL
    
    slippage_total: float
    slippage_impact_pct: float
    
    maker_ratio_entry: float  # % entries as maker
    maker_ratio_exit: float  # % exits as maker
    maker_ratio_overall: float
    
    # Timing
    avg_hold_minutes: float
    median_hold_minutes: float
    
    trades_per_day: float
    trades_per_symbol_per_day: float
    
    # Hit Rates
    stop_loss_hit_rate: float
    take_profit_hit_rate: float
    max_hold_exit_rate: float
    
    # Strategy Breakdown
    pnl_by_trigger: Dict[str, float] = None
    pnl_by_symbol: Dict[str, float] = None
    pnl_by_regime: Dict[str, float] = None
    
    # Warnings
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.pnl_by_trigger is None:
            self.pnl_by_trigger = {}
        if self.pnl_by_symbol is None:
            self.pnl_by_symbol = {}
        if self.pnl_by_regime is None:
            self.pnl_by_regime = {}
        if self.warnings is None:
            self.warnings = []


class PerformanceAnalyzer:
    """
    Calculate performance metrics from trade log.
    
    Supports:
    - Backtest analysis (historical trades)
    - Live monitoring (ongoing performance)
    - Comparative analysis (backtest vs live)
    """
    
    def __init__(self, trade_log: TradeLog):
        """
        Initialize analyzer.
        
        Args:
            trade_log: TradeLog instance with trades
        """
        self.trade_log = trade_log
    
    def analyze(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        symbol: Optional[str] = None,
        regime: Optional[str] = None
    ) -> PerformanceMetrics:
        """
        Calculate comprehensive performance metrics.
        
        Args:
            start_date: Filter trades after this date
            end_date: Filter trades before this date
            symbol: Filter to specific symbol
            regime: Filter to specific regime
            
        Returns:
            PerformanceMetrics object
        """
        # Build SQL query with filters
        where_clauses = ["exit_time IS NOT NULL"]  # Only closed trades
        
        if start_date:
            where_clauses.append(f"entry_time >= '{start_date.isoformat()}'")
        if end_date:
            where_clauses.append(f"entry_time <= '{end_date.isoformat()}'")
        if symbol:
            where_clauses.append(f"symbol = '{symbol}'")
        if regime:
            where_clauses.append(f"regime = '{regime}'")
        
        where_sql = " AND ".join(where_clauses)
        
        # Get trades
        trades = self.trade_log.query(f"""
            SELECT * FROM trades
            WHERE {where_sql}
            ORDER BY entry_time ASC
        """)
        
        if not trades:
            logger.warning("No closed trades found for analysis")
            return self._empty_metrics()
        
        # Parse dates
        for t in trades:
            if t['entry_time']:
                t['entry_time'] = datetime.fromisoformat(t['entry_time'])
            if t['exit_time']:
                t['exit_time'] = datetime.fromisoformat(t['exit_time'])
        
        # Period
        period_start = trades[0]['entry_time']
        period_end = trades[-1]['exit_time'] or datetime.now(timezone.utc)
        period_days = (period_end - period_start).total_seconds() / 86400
        
        # Basic counts
        total_trades = self.trade_log.query(f"SELECT COUNT(*) as count FROM trades WHERE {where_sql.replace('exit_time IS NOT NULL', '1=1')}")[0]['count']
        closed_trades = len(trades)
        open_trades = total_trades - closed_trades
        
        wins = [t for t in trades if t['pnl_net'] > 0]
        losses = [t for t in trades if t['pnl_net'] < 0]
        
        win_rate = (len(wins) / closed_trades * 100) if closed_trades > 0 else 0
        
        # PnL metrics
        pnls = [t['pnl_net'] for t in trades]
        returns = [t['pnl_pct'] for t in trades if t['pnl_pct'] is not None]
        
        pnl_total = sum(pnls)
        pnl_avg = sum(pnls) / len(pnls) if pnls else 0
        pnl_median = sorted(pnls)[len(pnls)//2] if pnls else 0
        pnl_std = self._std(pnls)
        
        return_pct_total = sum(returns) if returns else 0
        return_pct_avg = sum(returns) / len(returns) if returns else 0
        return_pct_annual = self._annualize_return(return_pct_avg, period_days)
        
        avg_win = sum(t['pnl_net'] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t['pnl_net'] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        
        largest_win = max((t['pnl_net'] for t in wins), default=0)
        largest_loss = min((t['pnl_net'] for t in losses), default=0)
        
        # Risk metrics
        sharpe = self._calculate_sharpe(returns, period_days)
        sortino = self._calculate_sortino(returns, period_days)
        
        max_dd, max_dd_duration = self._calculate_max_drawdown(trades)
        
        volatility_daily = self._std(returns) if returns else 0
        volatility_annual = volatility_daily * math.sqrt(252)  # 252 trading days
        
        # Streaks
        consecutive_losses_max = self._max_consecutive_losses(trades)
        consecutive_wins_max = self._max_consecutive_wins(trades)
        
        # MAE/MFE (requires intra-trade data - skip for now)
        mae_avg = 0.0
        mfe_avg = 0.0
        
        # Execution quality
        total_fees = sum(t['fees_total'] or 0 for t in trades)
        gross_pnl = sum(t['pnl_gross'] or 0 for t in trades)
        fee_impact = (total_fees / gross_pnl * 100) if gross_pnl > 0 else 0
        
        edge_pnl_total = sum(t['edge_pnl'] or 0 for t in trades)
        edge_capture = (edge_pnl_total / gross_pnl * 100) if gross_pnl > 0 else 0
        
        slippage_total = sum(t['slippage'] or 0 for t in trades)
        slippage_impact = (slippage_total / gross_pnl * 100) if gross_pnl > 0 else 0
        
        # Maker ratios
        entry_makers = sum(1 for t in trades if t['entry_is_maker'])
        exit_makers = sum(1 for t in trades if t['exit_is_maker'])
        maker_ratio_entry = (entry_makers / closed_trades * 100) if closed_trades > 0 else 0
        maker_ratio_exit = (exit_makers / closed_trades * 100) if closed_trades > 0 else 0
        maker_ratio_overall = ((entry_makers + exit_makers) / (closed_trades * 2) * 100) if closed_trades > 0 else 0
        
        # Timing
        hold_minutes = [t['hold_duration_minutes'] for t in trades if t['hold_duration_minutes']]
        avg_hold = sum(hold_minutes) / len(hold_minutes) if hold_minutes else 0
        median_hold = sorted(hold_minutes)[len(hold_minutes)//2] if hold_minutes else 0
        
        trades_per_day = closed_trades / period_days if period_days > 0 else 0
        
        unique_symbols = len(set(t['symbol'] for t in trades))
        trades_per_symbol_per_day = trades_per_day / unique_symbols if unique_symbols > 0 else 0
        
        # Hit rates
        stop_loss_hits = sum(1 for t in trades if t['exit_reason'] == 'stop_loss')
        take_profit_hits = sum(1 for t in trades if t['exit_reason'] == 'take_profit')
        max_hold_exits = sum(1 for t in trades if t['exit_reason'] == 'max_hold')
        
        stop_loss_rate = (stop_loss_hits / closed_trades * 100) if closed_trades > 0 else 0
        take_profit_rate = (take_profit_hits / closed_trades * 100) if closed_trades > 0 else 0
        max_hold_rate = (max_hold_exits / closed_trades * 100) if closed_trades > 0 else 0
        
        # Strategy breakdown
        pnl_by_trigger = self._group_pnl_by(trades, 'trigger_type')
        pnl_by_symbol = self._group_pnl_by(trades, 'symbol')
        pnl_by_regime = self._group_pnl_by(trades, 'regime')
        
        # Warnings
        warnings = []
        if win_rate < 30:
            warnings.append(f"Low win rate: {win_rate:.1f}%")
        if profit_factor < 1.5:
            warnings.append(f"Low profit factor: {profit_factor:.2f}")
        if max_dd > 20:
            warnings.append(f"High max drawdown: {max_dd:.1f}%")
        if sharpe < 1.0:
            warnings.append(f"Low Sharpe ratio: {sharpe:.2f}")
        if maker_ratio_overall < 50:
            warnings.append(f"Low maker ratio: {maker_ratio_overall:.1f}%")
        
        return PerformanceMetrics(
            start_date=period_start,
            end_date=period_end,
            days=period_days,
            total_trades=total_trades,
            closed_trades=closed_trades,
            open_trades=open_trades,
            wins=len(wins),
            losses=len(losses),
            win_rate_pct=win_rate,
            pnl_total=pnl_total,
            pnl_avg=pnl_avg,
            pnl_median=pnl_median,
            pnl_std=pnl_std,
            return_pct_total=return_pct_total,
            return_pct_avg=return_pct_avg,
            return_pct_annualized=return_pct_annual,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            largest_win=largest_win,
            largest_loss=largest_loss,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown_pct=max_dd,
            max_drawdown_duration_days=max_dd_duration,
            volatility_daily_pct=volatility_daily,
            volatility_annual_pct=volatility_annual,
            consecutive_losses_max=consecutive_losses_max,
            consecutive_wins_max=consecutive_wins_max,
            mae_avg=mae_avg,
            mfe_avg=mfe_avg,
            total_fees=total_fees,
            fee_impact_pct=fee_impact,
            edge_pnl_total=edge_pnl_total,
            edge_capture_pct=edge_capture,
            slippage_total=slippage_total,
            slippage_impact_pct=slippage_impact,
            maker_ratio_entry=maker_ratio_entry,
            maker_ratio_exit=maker_ratio_exit,
            maker_ratio_overall=maker_ratio_overall,
            avg_hold_minutes=avg_hold,
            median_hold_minutes=median_hold,
            trades_per_day=trades_per_day,
            trades_per_symbol_per_day=trades_per_symbol_per_day,
            stop_loss_hit_rate=stop_loss_rate,
            take_profit_hit_rate=take_profit_rate,
            max_hold_exit_rate=max_hold_rate,
            pnl_by_trigger=pnl_by_trigger,
            pnl_by_symbol=pnl_by_symbol,
            pnl_by_regime=pnl_by_regime,
            warnings=warnings
        )
    
    def _empty_metrics(self) -> PerformanceMetrics:
        """Return empty metrics when no trades"""
        now = datetime.now(timezone.utc)
        return PerformanceMetrics(
            start_date=now, end_date=now, days=0,
            total_trades=0, closed_trades=0, open_trades=0,
            wins=0, losses=0, win_rate_pct=0,
            pnl_total=0, pnl_avg=0, pnl_median=0, pnl_std=0,
            return_pct_total=0, return_pct_avg=0, return_pct_annualized=0,
            avg_win=0, avg_loss=0, profit_factor=0,
            largest_win=0, largest_loss=0,
            sharpe_ratio=0, sortino_ratio=0,
            max_drawdown_pct=0, max_drawdown_duration_days=0,
            volatility_daily_pct=0, volatility_annual_pct=0,
            consecutive_losses_max=0, consecutive_wins_max=0,
            mae_avg=0, mfe_avg=0,
            total_fees=0, fee_impact_pct=0,
            edge_pnl_total=0, edge_capture_pct=0,
            slippage_total=0, slippage_impact_pct=0,
            maker_ratio_entry=0, maker_ratio_exit=0, maker_ratio_overall=0,
            avg_hold_minutes=0, median_hold_minutes=0,
            trades_per_day=0, trades_per_symbol_per_day=0,
            stop_loss_hit_rate=0, take_profit_hit_rate=0, max_hold_exit_rate=0
        )
    
    def _std(self, values: List[float]) -> float:
        """Calculate standard deviation"""
        if not values:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)
    
    def _annualize_return(self, avg_return_pct: float, period_days: float) -> float:
        """Annualize return assuming daily compounding"""
        if period_days <= 0:
            return 0
        daily_return = avg_return_pct / 100
        annual_return = ((1 + daily_return) ** 252) - 1  # 252 trading days
        return annual_return * 100
    
    def _calculate_sharpe(self, returns: List[float], period_days: float) -> float:
        """Calculate Sharpe ratio (risk-adjusted return)"""
        if not returns or period_days <= 0:
            return 0
        
        avg_return = sum(returns) / len(returns)
        std_return = self._std(returns)
        
        if std_return == 0:
            return 0
        
        # Annualize
        annual_return = self._annualize_return(avg_return, period_days)
        annual_vol = std_return * math.sqrt(252)
        
        # Assume 0% risk-free rate
        sharpe = annual_return / annual_vol if annual_vol > 0 else 0
        return sharpe
    
    def _calculate_sortino(self, returns: List[float], period_days: float) -> float:
        """Calculate Sortino ratio (downside risk-adjusted)"""
        if not returns or period_days <= 0:
            return 0
        
        avg_return = sum(returns) / len(returns)
        
        # Downside deviation (only negative returns)
        downside_returns = [r for r in returns if r < 0]
        if not downside_returns:
            return 0
        
        downside_std = self._std(downside_returns)
        
        # Annualize
        annual_return = self._annualize_return(avg_return, period_days)
        annual_downside_vol = downside_std * math.sqrt(252)
        
        sortino = annual_return / annual_downside_vol if annual_downside_vol > 0 else 0
        return sortino
    
    def _calculate_max_drawdown(self, trades: List[Dict]) -> Tuple[float, float]:
        """Calculate max drawdown and duration"""
        if not trades:
            return 0, 0
        
        # Build cumulative PnL curve
        cumulative_pnl = []
        running_pnl = 0
        for t in trades:
            running_pnl += t['pnl_net']
            cumulative_pnl.append({
                'pnl': running_pnl,
                'time': t['exit_time']
            })
        
        # Find max drawdown
        max_dd = 0
        max_dd_duration = 0
        peak = cumulative_pnl[0]['pnl']
        peak_time = cumulative_pnl[0]['time']
        
        for point in cumulative_pnl:
            if point['pnl'] > peak:
                peak = point['pnl']
                peak_time = point['time']
            else:
                dd = ((peak - point['pnl']) / abs(peak)) * 100 if peak != 0 else 0
                if dd > max_dd:
                    max_dd = dd
                    duration = (point['time'] - peak_time).total_seconds() / 86400
                    max_dd_duration = max(max_dd_duration, duration)
        
        return max_dd, max_dd_duration
    
    def _max_consecutive_losses(self, trades: List[Dict]) -> int:
        """Find longest losing streak"""
        max_streak = 0
        current_streak = 0
        
        for t in trades:
            if t['pnl_net'] < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        
        return max_streak
    
    def _max_consecutive_wins(self, trades: List[Dict]) -> int:
        """Find longest winning streak"""
        max_streak = 0
        current_streak = 0
        
        for t in trades:
            if t['pnl_net'] > 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        
        return max_streak
    
    def _group_pnl_by(self, trades: List[Dict], field: str) -> Dict[str, float]:
        """Group PnL by field (trigger_type, symbol, regime)"""
        grouped = defaultdict(float)
        
        for t in trades:
            key = t.get(field) or 'unknown'
            grouped[key] += t['pnl_net']
        
        return dict(grouped)


class ReportGenerator:
    """Generate formatted performance reports"""
    
    def __init__(self, trade_log: TradeLog):
        self.trade_log = trade_log
        self.analyzer = PerformanceAnalyzer(trade_log)
    
    def generate_daily_report(self, output_dir: str = "reports") -> Path:
        """Generate daily performance report"""
        # Get today's trades
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        metrics = self.analyzer.analyze(start_date=today)
        
        # Create report
        report_dir = Path(output_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        
        report_file = report_dir / f"daily_{today.strftime('%Y%m%d')}.json"
        
        with open(report_file, 'w') as f:
            json.dump(asdict(metrics), f, indent=2, default=str)
        
        logger.info(f"Daily report generated: {report_file}")
        return report_file
    
    def generate_backtest_report(
        self,
        start_date: datetime,
        end_date: datetime,
        output_file: str = "reports/backtest_report.md"
    ) -> Path:
        """Generate comprehensive backtest report (Markdown)"""
        metrics = self.analyzer.analyze(start_date=start_date, end_date=end_date)
        
        report = self._format_markdown_report(metrics, title="Backtest Performance Report")
        
        report_path = Path(output_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(report_path, 'w') as f:
            f.write(report)
        
        logger.info(f"Backtest report generated: {report_path}")
        return report_path
    
    def _format_markdown_report(self, metrics: PerformanceMetrics, title: str) -> str:
        """Format metrics as Markdown report"""
        md = f"# {title}\n\n"
        md += f"**Period:** {metrics.start_date.strftime('%Y-%m-%d')} to {metrics.end_date.strftime('%Y-%m-%d')} ({metrics.days:.1f} days)\n\n"
        
        # Returns
        md += "## Returns\n\n"
        md += f"- **Total PnL:** ${metrics.pnl_total:.2f}\n"
        md += f"- **Total Return:** {metrics.return_pct_total:.2f}%\n"
        md += f"- **Annualized Return:** {metrics.return_pct_annualized:.2f}%\n"
        md += f"- **Avg PnL per Trade:** ${metrics.pnl_avg:.2f}\n"
        md += f"- **Median PnL:** ${metrics.pnl_median:.2f}\n\n"
        
        # Win/Loss
        md += "## Win/Loss Analysis\n\n"
        md += f"- **Closed Trades:** {metrics.closed_trades}\n"
        md += f"- **Open Trades:** {metrics.open_trades}\n"
        md += f"- **Win Rate:** {metrics.win_rate_pct:.1f}% ({metrics.wins}W / {metrics.losses}L)\n"
        md += f"- **Avg Win:** ${metrics.avg_win:.2f}\n"
        md += f"- **Avg Loss:** ${metrics.avg_loss:.2f}\n"
        md += f"- **Profit Factor:** {metrics.profit_factor:.2f}\n"
        md += f"- **Largest Win:** ${metrics.largest_win:.2f}\n"
        md += f"- **Largest Loss:** ${metrics.largest_loss:.2f}\n\n"
        
        # Risk
        md += "## Risk Metrics\n\n"
        md += f"- **Sharpe Ratio:** {metrics.sharpe_ratio:.2f}\n"
        md += f"- **Sortino Ratio:** {metrics.sortino_ratio:.2f}\n"
        md += f"- **Max Drawdown:** {metrics.max_drawdown_pct:.2f}% ({metrics.max_drawdown_duration_days:.1f} days)\n"
        md += f"- **Daily Volatility:** {metrics.volatility_daily_pct:.2f}%\n"
        md += f"- **Annual Volatility:** {metrics.volatility_annual_pct:.2f}%\n"
        md += f"- **Max Consecutive Losses:** {metrics.consecutive_losses_max}\n"
        md += f"- **Max Consecutive Wins:** {metrics.consecutive_wins_max}\n\n"
        
        # Execution
        md += "## Execution Quality\n\n"
        md += f"- **Total Fees:** ${metrics.total_fees:.2f} ({metrics.fee_impact_pct:.2f}% of gross PnL)\n"
        md += f"- **Edge Captured:** ${metrics.edge_pnl_total:.2f} ({metrics.edge_capture_pct:.2f}% of gross PnL)\n"
        md += f"- **Slippage:** ${metrics.slippage_total:.2f} ({metrics.slippage_impact_pct:.2f}% of gross PnL)\n"
        md += f"- **Maker Ratio (Entry):** {metrics.maker_ratio_entry:.1f}%\n"
        md += f"- **Maker Ratio (Exit):** {metrics.maker_ratio_exit:.1f}%\n"
        md += f"- **Maker Ratio (Overall):** {metrics.maker_ratio_overall:.1f}%\n\n"
        
        # Timing
        md += "## Timing\n\n"
        md += f"- **Avg Hold Time:** {metrics.avg_hold_minutes:.1f} minutes\n"
        md += f"- **Median Hold Time:** {metrics.median_hold_minutes:.1f} minutes\n"
        md += f"- **Trades per Day:** {metrics.trades_per_day:.2f}\n"
        md += f"- **Trades per Symbol per Day:** {metrics.trades_per_symbol_per_day:.2f}\n\n"
        
        # Exit reasons
        md += "## Exit Reasons\n\n"
        md += f"- **Stop Loss:** {metrics.stop_loss_hit_rate:.1f}%\n"
        md += f"- **Take Profit:** {metrics.take_profit_hit_rate:.1f}%\n"
        md += f"- **Max Hold:** {metrics.max_hold_exit_rate:.1f}%\n\n"
        
        # Strategy breakdown
        if metrics.pnl_by_trigger:
            md += "## PnL by Trigger Type\n\n"
            for trigger, pnl in sorted(metrics.pnl_by_trigger.items(), key=lambda x: x[1], reverse=True):
                md += f"- **{trigger}:** ${pnl:.2f}\n"
            md += "\n"
        
        if metrics.pnl_by_symbol:
            md += "## PnL by Symbol (Top 10)\n\n"
            top_symbols = sorted(metrics.pnl_by_symbol.items(), key=lambda x: x[1], reverse=True)[:10]
            for symbol, pnl in top_symbols:
                md += f"- **{symbol}:** ${pnl:.2f}\n"
            md += "\n"
        
        if metrics.pnl_by_regime:
            md += "## PnL by Regime\n\n"
            for regime, pnl in sorted(metrics.pnl_by_regime.items(), key=lambda x: x[1], reverse=True):
                md += f"- **{regime}:** ${pnl:.2f}\n"
            md += "\n"
        
        # Warnings
        if metrics.warnings:
            md += "## ⚠️ Warnings\n\n"
            for warning in metrics.warnings:
                md += f"- {warning}\n"
            md += "\n"
        
        return md
    
    def compare_backtest_vs_live(
        self,
        backtest_start: datetime,
        backtest_end: datetime,
        live_start: datetime,
        output_file: str = "reports/backtest_vs_live.json"
    ) -> Path:
        """Compare backtest and live performance"""
        # Get backtest metrics
        backtest_metrics = self.analyzer.analyze(
            start_date=backtest_start,
            end_date=backtest_end
        )
        
        # Get live metrics
        live_metrics = self.analyzer.analyze(start_date=live_start)
        
        # Calculate deltas
        comparison = {
            'backtest': asdict(backtest_metrics),
            'live': asdict(live_metrics),
            'deltas': {
                'win_rate_delta': live_metrics.win_rate_pct - backtest_metrics.win_rate_pct,
                'sharpe_delta': live_metrics.sharpe_ratio - backtest_metrics.sharpe_ratio,
                'return_delta': live_metrics.return_pct_annualized - backtest_metrics.return_pct_annualized,
                'max_dd_delta': live_metrics.max_drawdown_pct - backtest_metrics.max_drawdown_pct,
                'maker_ratio_delta': live_metrics.maker_ratio_overall - backtest_metrics.maker_ratio_overall,
                'fee_impact_delta': live_metrics.fee_impact_pct - backtest_metrics.fee_impact_pct
            }
        }
        
        # Save comparison
        report_path = Path(output_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(report_path, 'w') as f:
            json.dump(comparison, f, indent=2, default=str)
        
        logger.info(f"Backtest vs Live comparison: {report_path}")
        return report_path
