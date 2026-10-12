"""Turn a BacktestResult into a self-contained HTML report."""
import html
import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import REPORTS_DIR
from .backtest import BacktestResult
from .metrics import breakdown, equity_curve, monthly_returns

CSS = """
:root { --bg:#f7f7f5; --card:#fff; --text:#1d1d1f; --muted:#6b6b70; --line:#e4e4e0;
        --good:#1a7f4b; --bad:#c0392b; --good-bg:#e3f4ea; --bad-bg:#fbe5e2; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151517; --card:#1f1f22; --text:#ececee; --muted:#9a9aa1; --line:#333338;
          --good:#4cc38a; --bad:#ff6b5e; --good-bg:#16352a; --bad-bg:#3d1e1b; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1180px; margin:0 auto; padding:28px 16px 60px; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:18px; margin:32px 0 12px; }
.sub { color:var(--muted); margin:0 0 20px; }
.badge { display:inline-block; padding:2px 10px; border-radius:99px; font-size:12px;
         font-weight:600; border:1px solid var(--line); margin-left:8px; vertical-align:middle; }
.verdict { padding:16px 18px; border-radius:10px; margin:16px 0; font-weight:600; }
.verdict.pass { background:var(--good-bg); color:var(--good); }
.verdict.fail { background:var(--bad-bg); color:var(--bad); }
.verdict ul { margin:8px 0 0; padding-left:20px; font-weight:400; font-family:ui-monospace,Consolas,monospace; font-size:13px; }
.cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:10px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.card .k { color:var(--muted); font-size:12px; } .card .v { font-size:20px; font-weight:650; }
.grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; }
.panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; font-variant-numeric:tabular-nums; }
th, td { padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { color:var(--muted); font-weight:600; }
td.pos { color:var(--good); } td.neg { color:var(--bad); }
.scroll { max-height:520px; overflow:auto; }
.note { color:var(--muted); font-size:13px; }
"""


def _fmt(v, digits=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if isinstance(v, float):
        return "∞" if math.isinf(v) else f"{v:,.{digits}f}"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M")
    return html.escape(str(v))


def _table(df: pd.DataFrame, color_cols=(), index=True) -> str:
    if df is None or df.empty:
        return "<p class='note'>No data.</p>"
    cols = ([df.index.name or ""] if index else []) + [str(c) for c in df.columns]
    out = ["<table><thead><tr>", *[f"<th>{html.escape(c)}</th>" for c in cols], "</tr></thead><tbody>"]
    for idx, row in df.iterrows():
        out.append("<tr>")
        if index:
            out.append(f"<td>{_fmt(idx)}</td>")
        for c, v in row.items():
            cls = ""
            if c in color_cols and isinstance(v, (int, float)) and not pd.isna(v):
                cls = " class='pos'" if v > 0 else " class='neg'" if v < 0 else ""
            out.append(f"<td{cls}>{_fmt(v)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _cards(items: list[tuple[str, str]]) -> str:
    return "<div class='cards'>" + "".join(
        f"<div class='card'><div class='k'>{html.escape(k)}</div><div class='v'>{v}</div></div>"
        for k, v in items) + "</div>"


def _equity_chart(res: BacktestResult, start_balance: float) -> str:
    eq = equity_curve(res.trades, start_balance)
    dd = (eq.cummax() - eq) / eq.cummax() * -100
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name="Balance", line=dict(color="#2f6fde", width=2)), 1, 1)
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, name="Drawdown %", fill="tozeroy",
                             line=dict(color="#c0392b", width=1)), 2, 1)
    fig.update_layout(height=460, margin=dict(l=40, r=10, t=10, b=30), showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color="#888"))
    fig.update_yaxes(title_text="Balance ($)", row=1, col=1, gridcolor="rgba(128,128,128,0.2)")
    fig.update_yaxes(title_text="DD %", row=2, col=1, gridcolor="rgba(128,128,128,0.2)")
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.2)")
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def build_report(res: BacktestResult, cfg: dict) -> str:
    bal = cfg["account"]["starting_balance"]
    s, mc, prop, t = res.summary, res.monte_carlo, res.prop, res.trades
    sample = "OUT-OF-SAMPLE" if res.out_of_sample else "IN-SAMPLE"
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Backtest {html.escape(res.strategy)}</title><style>{CSS}</style></head><body><main>",
             f"<h1>{html.escape(res.strategy)}<span class='badge'>{sample}</span></h1>",
             f"<p class='sub'>{html.escape(res.description)}<br>"
             f"{', '.join(res.symbols)} · {res.period} · start ${bal:,.0f} · "
             f"risk {cfg['risk']['risk_per_trade_pct']}% per trade · "
             f"generated {datetime.now():%Y-%m-%d %H:%M}</p>"]

    vclass = "pass" if res.passed else "fail"
    vtitle = ("PASSES the minimum bar for the next stage" if res.passed
              else "DOES NOT pass. Do not trade this version.")
    parts.append(f"<div class='verdict {vclass}'>{vtitle}<ul>"
                 + "".join(f"<li>{html.escape(c)}</li>" for c in res.checks) + "</ul></div>")

    if not s.get("trades"):
        parts.append("<p>No trades were taken.</p></main></body></html>")
        return "".join(parts)

    def money(v):
        cls = "pos" if v > 0 else "neg"
        return f"<span class='{cls}'>${v:,.0f}</span>"

    parts.append(_cards([
        ("Net profit", money(s["net_profit"])),
        ("Return (total)", f"{s['total_return_pct']:.1f}%"),
        ("Avg month (compounded)", f"{s['monthly_return_pct']:.2f}%"),
        ("Trades", f"{s['trades']} <span class='note'>({s['trades_per_month']:.1f}/mo)</span>"),
        ("Win rate", f"{s['win_rate_pct']:.1f}%"),
        ("Expectancy", f"{s['expectancy_r']:+.2f}R"),
        ("Profit factor", _fmt(s["profit_factor"])),
        ("Max drawdown", f"{s['max_drawdown_pct']:.1f}%"),
        ("Avg win / loss", f"{s['avg_win_r']:+.2f}R / {s['avg_loss_r']:+.2f}R"),
        ("Longest losing streak", str(s["longest_losing_streak"])),
    ]))

    parts.append("<h2>Balance and drawdown</h2><div class='panel'>" + _equity_chart(res, bal) + "</div>")

    parts.append("<h2>Stress test and prop firm simulation</h2><div class='grid2'>")
    if mc:
        parts.append("<div class='panel'><b>Monte Carlo</b><p class='note'>The same trades shuffled into "
                     f"{mc['runs']:,} random orders: how bad could the drawdown have been with worse luck?</p>"
                     + _cards([("Median max DD", f"{mc['median_max_dd_pct']:.1f}%"),
                               ("95th pct max DD", f"{mc['p95_max_dd_pct']:.1f}%"),
                               ("Chance DD > 10%", f"{mc['prob_dd_over_10_pct']:.0f}%"),
                               ("Chance DD > 20%", f"{mc['prob_dd_over_20_pct']:.0f}%")]) + "</div>")
    if prop:
        p = cfg["prop"]
        parts.append("<div class='panel'><b>Prop challenge</b><p class='note'>A new challenge started on the 1st of "
                     f"every month: +{p['profit_target_pct']}% target, -{p['max_daily_loss_pct']}% daily, "
                     f"-{p['max_total_loss_pct']}% total.</p>"
                     + _cards([("Pass rate", _fmt(prop["pass_rate_pct"], 0) + "%"),
                               ("Passed / failed", f"{prop['passed']} / {prop['failed']}"),
                               ("Unfinished", str(prop["unfinished"])),
                               ("Median days to pass", _fmt(prop["median_days_to_pass"], 0))]) + "</div>")
    parts.append("</div>")

    parts.append("<h2>Monthly returns (%)</h2><div class='panel'>"
                 + _table(monthly_returns(t).round(2), color_cols=set(monthly_returns(t).columns)) + "</div>")

    t2 = t.assign(year=t.exit_time.dt.year)
    parts.append("<h2>Breakdowns</h2><div class='grid2'>")
    for title, by in (("By symbol", "symbol"), ("By direction", "side"),
                      ("By year", "year"), ("By exit", "exit_reason")):
        parts.append(f"<div class='panel'><b>{title}</b>"
                     + _table(breakdown(t2, by), color_cols={"expectancy_r", "net_profit"}) + "</div>")
    parts.append("</div>")

    funnel = pd.DataFrame({"count": {"signals generated": res.signals, "trades taken": len(t),
                                     **{f"skipped: {k}": v for k, v in res.skipped.items() if v}}})
    funnel.index.name = "signal funnel"
    params = pd.DataFrame({"value": {k: str(v) for k, v in res.params.items()}})
    params.index.name = "parameter"
    parts.append("<div class='grid2' style='margin-top:16px'>"
                 f"<div class='panel'>{_table(funnel)}</div><div class='panel'>{_table(params)}</div></div>")

    cols = ["fill_time", "symbol", "order", "entry", "sl", "tp1", "tp2", "fill_price", "exit_time",
            "exit_reason", "lots", "r", "pnl", "balance_after"]
    trades_view = t[cols].copy()
    trades_view[["r", "pnl", "balance_after"]] = trades_view[["r", "pnl", "balance_after"]].round(2)
    trades_view.index = trades_view.index + 1
    trades_view.index.name = "#"
    parts.append(f"<h2>All trades ({len(t)})</h2><div class='panel scroll'>"
                 + _table(trades_view, color_cols={"r", "pnl"}) + "</div>")
    parts.append("<p class='note'>Fills use bid/ask with spread and slippage; if SL and TP fall inside the "
                 "same candle the loss is assumed. Past results do not guarantee future results.</p>")
    parts.append("</main></body></html>")
    return "".join(parts)


def save_report(res: BacktestResult, cfg: dict, out_dir: Path = REPORTS_DIR) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = "OOS" if res.out_of_sample else "IS"
    path = out_dir / f"{res.strategy}_{tag}_{datetime.now():%Y%m%d_%H%M%S}.html"
    path.write_text(build_report(res, cfg), encoding="utf-8")
    return path
