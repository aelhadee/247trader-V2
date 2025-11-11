"""Configuration validation tooling for 247trader-v2.

Usage:
    python -m tools.config_check            # validate standard config files
    python -m tools.config_check --files config/app.yaml config/policy.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Iterable, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ---------------------------------------------------------------------------
# Pydantic models describing the expected structure of the YAML files.
# The goal for Phase 0 is to catch the most common misconfigurations while
# remaining permissive enough to accommodate future fields.
# ---------------------------------------------------------------------------


class AppMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    mode: Literal["DRY_RUN", "PAPER", "LIVE"]


class RateLimit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    public: int = Field(ge=0)
    private: int = Field(ge=0)


class ExchangeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    read_only: bool
    rate_limit: Optional[RateLimit]


class LoopConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    interval_minutes: float = Field(gt=0)
    timezone: str = Field(min_length=1)


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    format: Literal["json", "text"]
    file: str
    rotate: bool


class StateConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    store: Literal["sqlite", "redis", "memory"]
    path: str


class MonitoringConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    healthcheck_port: int = Field(ge=0)
    metrics_port: int = Field(ge=0)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    app: AppMetadata
    exchange: ExchangeConfig
    loop: LoopConfig
    logging: LoggingConfig
    state: StateConfig
    monitoring: MonitoringConfig


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_total_at_risk_pct: float = Field(gt=0)
    max_position_size_pct: float = Field(gt=0)
    min_trade_notional_usd: float = Field(gt=0)


class PositionSizingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    method: Literal["fixed", "risk_parity", "kelly"]
    min_order_usd: Optional[float] = Field(default=None, gt=0)


class LiquidityConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    min_24h_volume_usd: float = Field(gt=0)
    max_spread_bps: float = Field(gt=0)


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    default_order_type: Literal["limit_post_only", "market"]
    max_slippage_bps: Optional[float] = Field(default=None, ge=0)


class PortfolioManagementConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    auto_liquidate_ineligible: bool
    min_liquidation_value_usd: float = Field(gt=0)


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    risk: RiskConfig
    position_sizing: PositionSizingConfig
    liquidity: LiquidityConfig
    execution: ExecutionConfig
    portfolio_management: PortfolioManagementConfig


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    clusters: Dict[str, object]
    exclusions: Dict[str, object]
    liquidity: Dict[str, object]
    tiers: Dict[str, object]
    universe: Dict[str, object]


# Mapping from config file to validation model
DEFAULT_MODELS: Dict[str, type[BaseModel]] = {
    "config/app.yaml": AppConfig,
    "config/policy.yaml": PolicyConfig,
    "config/universe.yaml": UniverseConfig,
}


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise TypeError(f"Expected mapping at root of {path}, got {type(data).__name__}")
    return data


def validate_file(path: Path, model: type[BaseModel]) -> list[str]:
    try:
        data = load_yaml(path)
        model.model_validate(data)
    except FileNotFoundError:
        return [f"✖ {path}: file not found"]
    except (TypeError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            details = [f"  - {err['loc']}: {err['msg']}" for err in exc.errors()]
            return [f"✖ {path} invalid"] + details
        return [f"✖ {path}: {exc}"]
    else:
        return [f"✓ {path} valid"]


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate 247trader-v2 configuration files")
    parser.add_argument("--files", nargs="*", help="Specific config files to validate")
    args = parser.parse_args(list(argv) if argv is not None else None)

    targets = args.files if args.files else DEFAULT_MODELS.keys()

    exit_code = 0
    for target in targets:
        path = Path(target)
        model = DEFAULT_MODELS.get(target)
        if model is None:
            print(f"! {target}: no schema registered (skipping)")
            continue

        messages = validate_file(path, model)
        for line in messages:
            print(line)
        if messages[0].startswith("✖"):
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
