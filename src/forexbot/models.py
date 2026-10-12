from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Signal:
    """One trade idea, exactly as it would be sent to the phone."""
    strategy: str
    symbol: str
    side: str                       # "buy" | "sell"
    order_type: str                 # "market" | "limit" | "stop"
    created: pd.Timestamp           # UTC; the moment the signal is known (close of last bar used)
    entry: float
    sl: float
    tp1: float | None               # None = no fixed target (exit by trailing stop / time)
    tp2: float | None = None
    tp1_close_pct: float = 100.0    # % of the position closed at TP1 (when TP2 is set)
    move_sl_to_be: bool = True      # move SL to entry once TP1 is hit
    expires: pd.Timestamp | None = None   # pending order cancelled if not filled by then
    close_by: pd.Timestamp | None = None  # open position closed at market at this time
    oco_group: str | None = None    # orders in a group cancel each other once one fills
    cancel_if_tp_before_fill: bool = True
    trail_distance: float | None = None   # trailing stop: SL follows the best price at this distance
    reason: str = ""

    def __post_init__(self):
        sign = 1 if self.side == "buy" else -1
        if self.side not in ("buy", "sell") or self.order_type not in ("market", "limit", "stop"):
            raise ValueError(f"bad side/order_type: {self.side} {self.order_type}")
        if not sign * (self.entry - self.sl) > 0:
            raise ValueError(f"SL on the wrong side of entry: {self}")
        if self.tp1 is not None and not sign * (self.tp1 - self.entry) > 0:
            raise ValueError(f"TP1 on the wrong side of entry: {self}")
        if self.tp2 is not None and (self.tp1 is None or sign * (self.tp2 - self.tp1) <= 0):
            raise ValueError(f"TP2 must be beyond TP1: {self}")
        if self.trail_distance is not None and self.trail_distance <= 0:
            raise ValueError(f"trail_distance must be positive: {self}")

    @property
    def direction(self) -> int:
        return 1 if self.side == "buy" else -1

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def label(self) -> str:
        if self.order_type == "market":
            return f"{self.side.upper()} NOW (market)"
        return f"{self.side.upper()} {self.order_type.upper()}"


@dataclass
class Exit:
    time: pd.Timestamp
    price: float
    fraction: float     # share of the original position closed
    reason: str         # tp1 | tp2 | sl | be | time | data_end


@dataclass
class SimResult:
    """What happened to one signal, independent of account size."""
    signal: Signal
    status: str = "pending"         # filled | expired | cancelled | no_fill
    fill_time: pd.Timestamp | None = None
    fill_price: float | None = None
    exits: list[Exit] = field(default_factory=list)

    @property
    def filled(self) -> bool:
        return self.status == "filled"

    @property
    def exit_time(self) -> pd.Timestamp | None:
        return self.exits[-1].time if self.exits else None

    @property
    def exit_reason(self) -> str:
        return self.exits[-1].reason if self.exits else ""

    @property
    def r_multiple(self) -> float:
        """Profit in units of the planned risk (entry to SL). Costs included."""
        s = self.signal
        gain = sum(e.fraction * (e.price - self.fill_price) * s.direction for e in self.exits)
        return gain / s.stop_distance
