"""Read/write candle files: data/<SYMBOL>_<TF>.csv.gz, times in UTC."""
from pathlib import Path

import pandas as pd

from . import DATA_DIR


def bars_path(symbol: str, timeframe: str, data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / f"{symbol}_{timeframe}.csv.gz"


def save_bars(df: pd.DataFrame, symbol: str, timeframe: str, data_dir: Path = DATA_DIR) -> Path:
    path = bars_path(symbol, timeframe, data_dir)
    out = df.copy()
    out.index.name = "time"
    out.to_csv(path, compression="gzip")
    return path


def load_bars(symbol: str, timeframe: str, data_dir: Path = DATA_DIR,
              start=None, end=None) -> pd.DataFrame:
    path = bars_path(symbol, timeframe, data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No data for {symbol} {timeframe} ({path.name}). Run scripts/download_data.py first."
        )
    df = pd.read_csv(path, compression="gzip")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("time"), utc=True)).as_unit("ns")
    df = df.sort_index()
    if start is not None:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df.index < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]
    return df
