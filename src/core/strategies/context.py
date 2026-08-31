"""Provider-neutral market snapshots consumed by strategies."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

@dataclass(frozen=True)
class InstrumentSnapshot:
    symbol: str
    name: Optional[str] = None
    market: str = "cn"
    instrument_type: str = "convertible_bond"
    tradable: bool = True

@dataclass(frozen=True)
class Bar:
    trade_date: date
    close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None

@dataclass(frozen=True)
class FactorSnapshot:
    premium_rate: Optional[float] = None
    remaining_size: Optional[float] = None
    values: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class MarketEvent:
    event_type: str
    event_date: Optional[date] = None
    details: dict[str, Any] = field(default_factory=dict)
    blocking: bool = True

@dataclass(frozen=True)
class PositionSnapshot:
    quantity: float = 0
    available_quantity: Optional[float] = None
    cost_price: Optional[float] = None

    @property
    def available(self) -> float:
        return self.quantity if self.available_quantity is None else self.available_quantity

@dataclass(frozen=True)
class MarketContext:
    as_of: datetime
    market: str
    instrument_type: str
    instruments: list[InstrumentSnapshot] = field(default_factory=list)
    bars: dict[str, list[Bar]] = field(default_factory=dict)
    factors: dict[str, FactorSnapshot] = field(default_factory=dict)
    events: dict[str, list[MarketEvent]] = field(default_factory=dict)
    positions: dict[str, PositionSnapshot] = field(default_factory=dict)
    cash: Optional[float] = None
    account: Optional[str] = None
