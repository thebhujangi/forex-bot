"""Contract specs, lot sizing and trading costs per symbol."""
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from . import DATA_DIR


@dataclass
class SymbolSpec:
    symbol: str                 # our name, e.g. XAUUSD
    broker_symbol: str          # the broker's name, e.g. XAUUSD.m
    digits: int
    contract_size: float        # units per 1.00 lot (gold: ounces, FX: base currency)
    currency_base: str
    currency_profit: str
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 100.0

    def money_per_price_unit(self, lots: float, price: float) -> float:
        """USD gained/lost per 1.0 move in price for this many lots."""
        if self.currency_profit == "USD":
            return self.contract_size * lots
        if self.currency_base == "USD":
            return self.contract_size * lots / price
        raise ValueError(f"{self.symbol}: only USD-quoted or USD-based symbols are supported")

    def round_lots(self, lots: float) -> float:
        """Round down to the broker's lot step. Returns 0 if below the minimum lot."""
        steps = math.floor(lots / self.volume_step + 1e-9)
        lots = round(min(steps * self.volume_step, self.volume_max), 8)
        return lots if lots >= self.volume_min else 0.0

    def fmt(self, price: float) -> str:
        return f"{price:.{self.digits}f}"


DEFAULT_SPECS = {
    "XAUUSD": SymbolSpec("XAUUSD", "XAUUSD", 2, 100, "XAU", "USD"),
    "EURUSD": SymbolSpec("EURUSD", "EURUSD", 5, 100_000, "EUR", "USD"),
    "GBPUSD": SymbolSpec("GBPUSD", "GBPUSD", 5, 100_000, "GBP", "USD"),
    "AUDUSD": SymbolSpec("AUDUSD", "AUDUSD", 5, 100_000, "AUD", "USD"),
    "USDJPY": SymbolSpec("USDJPY", "USDJPY", 3, 100_000, "USD", "JPY"),
}

SPECS_FILE = "symbols.json"


def load_specs(data_dir: Path = DATA_DIR) -> dict[str, SymbolSpec]:
    """Built-in defaults, overridden by the broker's real specs once downloaded.
    The project-level symbols.json (shared copy, used in the cloud) is the fallback."""
    from . import PROJECT_ROOT
    specs = dict(DEFAULT_SPECS)
    for path in (PROJECT_ROOT / SPECS_FILE, Path(data_dir) / SPECS_FILE):
        if path.exists():
            for sym, d in json.loads(path.read_text(encoding="utf-8")).items():
                specs[sym] = SymbolSpec(**d)
    return specs


def save_specs(specs: dict[str, SymbolSpec], data_dir: Path = DATA_DIR) -> None:
    path = Path(data_dir) / SPECS_FILE
    path.write_text(json.dumps({k: asdict(v) for k, v in specs.items()}, indent=2), encoding="utf-8")


@dataclass
class Costs:
    spread: float = 0.0             # price units, added to bid to get ask
    slippage: float = 0.0           # price units, applied to stop entries and stop losses
    commission_per_lot: float = 0.0  # USD per 1.00 lot, round trip
    spread_pct: float = 0.0         # % of price (crypto: prices change 10x over the years)
    slippage_pct: float = 0.0

    def at_price(self, price: float) -> "Costs":
        """Absolute costs for a trade at this price."""
        return Costs(self.spread + price * self.spread_pct / 100,
                     self.slippage + price * self.slippage_pct / 100, self.commission_per_lot)


def load_costs(cfg: dict, symbol: str) -> Costs:
    return Costs(**cfg.get("costs", {}).get(symbol, {}))
