from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Optional
from .context import MarketContext

@dataclass(frozen=True)
class StrategyDecision:
    action: str
    symbol: Optional[str] = None
    symbol_name: Optional[str] = None
    target_weight: Optional[float] = None
    target_amount: Optional[float] = None
    suggested_quantity: Optional[float] = None
    reason: str = ""
    decision_data: dict[str, Any] = field(default_factory=dict)
    risk_status: str = "passed"
    decision_uid: Optional[str] = None

class StrategyBase(ABC):
    strategy_id = "abstract"
    version = "v1"
    instrument_types = ("convertible_bond",)
    markets = ("cn",)
    parameter_definitions: tuple[dict[str, Any], ...] = ()

    def validate_context(self, context: MarketContext) -> None:
        if context.market not in self.markets or context.instrument_type not in self.instrument_types:
            raise ValueError("strategy does not support this market/instrument type")

    def parameters(self, overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        values = {p["key"]: p.get("default") for p in self.parameter_definitions}
        values.update(overrides or {})
        for p in self.parameter_definitions:
            key = p["key"]
            if key not in values: continue
            if p.get("min") is not None and values[key] < p["min"]: raise ValueError(f"{key} below minimum")
            if p.get("max") is not None and values[key] > p["max"]: raise ValueError(f"{key} above maximum")
        return values

    @abstractmethod
    def evaluate(self, context: MarketContext, *, mode: Literal["rebalance", "event_check"] = "rebalance", parameters: Optional[dict[str, Any]] = None) -> list[StrategyDecision]: ...
