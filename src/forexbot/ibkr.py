"""Live futures prices from Interactive Brokers (read-only).

Talks to IB Gateway or Trader Workstation (TWS) running on this PC, logged in to your IBKR
account (paper or live). Read-only: the connection is opened with readonly=True, so it cannot
place, change or cancel orders.

Ports (set in IB Gateway/TWS: Configure > Settings > API): IB Gateway paper 4002, live 4001;
TWS paper 7497, live 7496. Real-time CME prices need the "CME Real-Time (NP, L1)" market data
subscription in IBKR's Client Portal (about $1.55/month, waived above $20/month commissions).
"""
import math

import pandas as pd

from .feeds import FeedError
from .timeutils import TIMEFRAME_MINUTES

BAR_SIZE = {"M1": "1 min", "M5": "5 mins", "M15": "15 mins", "H1": "1 hour"}


class IbkrMarket:
    """Same interface as feeds.Mt5Market: bars(), price(), doubt()."""
    has_account = False

    def __init__(self, host: str = "127.0.0.1", port: int = 4002, client_id: int = 17):
        from ib_async import IB, ContFuture
        self.ib = IB()
        try:
            self.ib.connect(host, port, clientId=client_id, readonly=True, timeout=10)
        except Exception as e:
            raise FeedError(f"can't reach IB Gateway/TWS on port {port}: {e}") from None
        self._contracts: dict = {}
        self._ContFuture = ContFuture

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()

    def contract(self, symbol: str):
        """The front-month contract of a CME future, e.g. MNQ (rolls over by itself)."""
        if symbol not in self._contracts:
            found = self.ib.qualifyContracts(self._ContFuture(symbol, exchange="CME"))
            if not found:
                raise FeedError(f"IBKR doesn't know the future {symbol}")
            self._contracts[symbol] = found[0]
        return self._contracts[symbol]

    def bars(self, symbol: str, tf: str, count: int) -> pd.DataFrame:
        """The latest `count` closed candles in UTC (traded prices, all hours)."""
        minutes = TIMEFRAME_MINUTES[tf]
        days = math.ceil(count * minutes / 1380) + 1          # CME trades ~23 hours a day
        rows = self.ib.reqHistoricalData(self.contract(symbol), endDateTime="", durationStr=f"{days} D",
                                         barSizeSetting=BAR_SIZE[tf], whatToShow="TRADES", useRTH=False,
                                         formatDate=2)
        if not rows:
            raise FeedError(f"IBKR sent no {symbol} candles (market data subscription missing?)")
        df = pd.DataFrame([(b.date, b.open, b.high, b.low, b.close) for b in rows],
                          columns=["time", "open", "high", "low", "close"])
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("time"), utc=True)).as_unit("ns")
        df = df[df.index + pd.Timedelta(minutes=minutes) <= pd.Timestamp.now(tz="UTC")]
        return df.tail(count)

    def price(self, symbol: str, side: str) -> float | None:
        b = self.bars(symbol, "M1", 2)
        return float(b.close.iloc[-1]) if len(b) else None

    def doubt(self, symbol: str, price: float) -> float:
        return 0.0          # the futures' own prices
