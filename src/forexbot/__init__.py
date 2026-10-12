"""Forex Bot: gold & forex signal bot (backtest -> demo signals -> prop firm)."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"            # downloads, caches, logs (not shared)
REPORTS_DIR = PROJECT_ROOT / "reports"
# The bot's memory: open signals, history, crowd snapshots. The cloud copy saves this folder
# between runs, so it's kept separate from the big data files.
STATE_DIR = Path(os.environ.get("FOREXBOT_STATE_DIR", PROJECT_ROOT / "state"))
for _d in (DATA_DIR, STATE_DIR):
    _d.mkdir(exist_ok=True)
