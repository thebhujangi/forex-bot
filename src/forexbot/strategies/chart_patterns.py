"""Chart patterns: classic shapes, harmonic patterns, price-action setups and candlesticks.

How a shape becomes a rule a computer can test:
  * Swing points ("pivots"): a high is confirmed once price has fallen `pivot_atr` x ATR from it, a
    low once price has risen that much. A pivot is only used from the bar that confirmed it, so
    nothing ever uses information from the future.
  * Each pattern is a set of relations between the last few pivots. All rules below are written for
    the BULLISH version; the bearish one is the same rules on the price chart turned upside down.
  * Breakout patterns trigger on a candle CLOSE through the pattern's line (not a touch).
  * Textbook trade: enter at the next candle, stop beyond the pattern, target = measured move,
    and a time limit (3x the pattern's length, 10-100 candles).
The same function is used for backtests and live signals.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..instruments import SymbolSpec
from ..models import Signal
from ..timeutils import TIMEFRAME_MINUTES

NAME = "chart_patterns"
DESCRIPTION = "Chart patterns: classic shapes, harmonics, price action and candlesticks"

CLASSIC = ("double_bottom", "triple_bottom", "head_shoulders", "asc_triangle", "sym_triangle", "falling_wedge",
           "flag", "pennant", "rectangle", "broadening", "cup_handle", "rounding", "diamond", "channel_break")
PRICE_ACTION = ("one_two_three", "quasimodo", "break_retest", "false_break_2b", "three_drives", "wolfe_wave",
                "rsi_divergence")
HARMONIC = ("abcd", "gartley", "bat", "butterfly", "crab", "cypher", "shark")
CANDLES = ("engulfing", "pin_bar", "star", "piercing", "harami", "three_soldiers", "tweezer", "three_outside",
           "three_inside", "doji_reversal", "marubozu", "inside_bar", "nr7", "outside_bar", "shark32")
ALL = CLASSIC + PRICE_ACTION + HARMONIC + CANDLES

# (bullish name, bearish name) as traders call them
LABELS = {
    "double_bottom": ("Double bottom", "Double top"), "triple_bottom": ("Triple bottom", "Triple top"),
    "head_shoulders": ("Inverse head and shoulders", "Head and shoulders"),
    "asc_triangle": ("Ascending triangle", "Descending triangle"),
    "sym_triangle": ("Symmetrical triangle (up)", "Symmetrical triangle (down)"),
    "falling_wedge": ("Falling wedge", "Rising wedge"), "flag": ("Bull flag", "Bear flag"),
    "pennant": ("Bull pennant", "Bear pennant"), "rectangle": ("Rectangle (up)", "Rectangle (down)"),
    "broadening": ("Expanding triangle (up)", "Expanding triangle (down)"),
    "cup_handle": ("Cup and handle", "Inverse cup and handle"), "rounding": ("Rounding bottom", "Rounding top"),
    "diamond": ("Diamond bottom", "Diamond top"), "channel_break": ("Falling channel break", "Rising channel break"),
    "one_two_three": ("1-2-3 bottom", "1-2-3 top"), "quasimodo": ("Bullish Quasimodo", "Bearish Quasimodo"),
    "break_retest": ("Break and retest (up)", "Break and retest (down)"),
    "false_break_2b": ("2B false breakdown", "2B false breakout"),
    "three_drives": ("Three drives (bullish)", "Three drives (bearish)"),
    "wolfe_wave": ("Bullish Wolfe wave", "Bearish Wolfe wave"),
    "rsi_divergence": ("Bullish RSI divergence", "Bearish RSI divergence"),
    "abcd": ("Bullish AB=CD", "Bearish AB=CD"), "gartley": ("Bullish Gartley", "Bearish Gartley"),
    "bat": ("Bullish Bat", "Bearish Bat"), "butterfly": ("Bullish Butterfly", "Bearish Butterfly"),
    "crab": ("Bullish Crab", "Bearish Crab"), "cypher": ("Bullish Cypher", "Bearish Cypher"),
    "shark": ("Bullish Shark", "Bearish Shark"), "engulfing": ("Bullish engulfing", "Bearish engulfing"),
    "pin_bar": ("Hammer / pin bar", "Shooting star / pin bar"), "star": ("Morning star", "Evening star"),
    "piercing": ("Piercing line", "Dark cloud cover"), "harami": ("Bullish harami", "Bearish harami"),
    "three_soldiers": ("Three white soldiers", "Three black crows"), "tweezer": ("Tweezer bottom", "Tweezer top"),
    "three_outside": ("Three outside up", "Three outside down"),
    "three_inside": ("Three inside up", "Three inside down"),
    "doji_reversal": ("Dragonfly doji", "Gravestone doji"), "marubozu": ("Bullish marubozu", "Bearish marubozu"),
    "inside_bar": ("Inside bar break (up)", "Inside bar break (down)"),
    "nr7": ("NR7 break (up)", "NR7 break (down)"),
    "outside_bar": ("Bullish outside bar", "Bearish outside bar"),
    "shark32": ("Shark-32 break (up)", "Shark-32 break (down)"),
}

MAX_LEN, MAX_WAIT = 150, 60            # candles: longest pattern, longest wait for the breakout


def atr_of(h, l, c, n=20):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def rsi_of(c, n=14):
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.maximum(d, 0)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.maximum(-d, 0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).to_numpy()


def pivots(h, l, atr, k):
    """Alternating swing points as (bar, price, +1 high / -1 low, bar at which it was confirmed)."""
    out, direction = [], 0
    hi = lo = None
    for i in range(len(h)):
        a = atr[i]
        if not a > 0:
            continue
        if hi is None:
            hi, lo = (i, h[i]), (i, l[i])
            continue
        if direction >= 0 and h[i] >= hi[1]:
            hi = (i, h[i])
            if direction == 1:
                continue
        if direction <= 0 and l[i] <= lo[1]:
            lo = (i, l[i])
            if direction == -1:
                continue
        if direction >= 0 and hi[0] < i and hi[1] - l[i] >= k * a:
            out.append((hi[0], hi[1], 1, i))
            direction, lo = -1, (i, l[i])
        elif direction <= 0 and lo[0] < i and h[i] - lo[1] >= k * a:
            out.append((lo[0], lo[1], -1, i))
            direction, hi = 1, (i, h[i])
    return out


def _line(p, q):
    """Straight line through two pivots, as a function of the bar number."""
    b = (q[1] - p[1]) / (q[0] - p[0])
    return lambda t: p[1] + b * (t - p[0])


def _flat(level):
    return lambda t: level


def _breakouts(P):
    """Bullish breakout patterns known once the low P[-1] is confirmed.
    Yields (name, line the close must break, stop, target height or ('abs', price), first bar)."""
    L0, H0, L1, H1 = P[-1], P[-2], P[-3], P[-4]
    top, bot = max(H0[1], H1[1]), min(L0[1], L1[1])
    hgt = top - bot
    if hgt <= 0:
        return
    H2 = P[-6] if len(P) >= 6 else None
    L2 = P[-5] if len(P) >= 5 else None
    flat_h, flat_l = abs(H0[1] - H1[1]) <= 0.2 * hgt, abs(L0[1] - L1[1]) <= 0.2 * hgt
    dh, dl = H0[1] - H1[1], L0[1] - L1[1]
    start, upper, neck = H1[0], _line(H1, H0), H0[1]

    # --- two lows, one neckline ---
    h1 = neck - bot
    if h1 > 0 and abs(L0[1] - L1[1]) <= 0.2 * h1 and H1[1] > neck + 0.5 * h1:
        yield "double_bottom", _flat(neck), bot, h1, L1[0]
    if h1 > 0 and L0[1] > L1[1] + 0.2 * h1 and dh < 0 and L2 is not None and L1[1] < L2[1]:
        yield "one_two_three", _flat(neck), L0[1], h1, L1[0]

    # --- boxes, triangles, wedges, channels (two highs, two lows) ---
    r_old, r_new = H1[1] - L1[1], H0[1] - L0[1]
    conv = r_new < 0.75 * r_old
    pole = (H1[1] - L2[1]) if L2 is not None else 0.0
    has_pole = L2 is not None and pole >= 2.0 * hgt and (H1[0] - L2[0]) <= 1.5 * max(L0[0] - H1[0], 1)
    if flat_h and flat_l and L2 is not None and L2[1] < bot - 0.5 * hgt:
        yield "rectangle", _flat(top), bot, hgt, start
    if flat_h and dl > 0.25 * hgt:
        depth, cup_len, handle = top - L1[1], H0[0] - H1[0], L0[0] - H0[0]
        if L0[1] >= L1[1] + 0.5 * depth and cup_len >= 25 and cup_len >= 2 * max(handle, 1):
            yield "cup_handle", _flat(top), L0[1], depth, start
        else:
            yield "asc_triangle", _flat(top), L0[1], hgt, start
    if dh < -0.15 * hgt and dl > 0.15 * hgt:
        yield ("pennant" if has_pole else "sym_triangle"), upper, L0[1], (pole if has_pole else hgt), start
    if dh < -0.15 * hgt and dl < -0.05 * hgt:
        if conv:
            yield "falling_wedge", upper, L0[1], ("abs", H1[1]), start
        elif 0.6 <= (dl / (L0[0] - L1[0])) / (dh / (H0[0] - H1[0])) <= 1.6:
            yield ("flag" if has_pole else "channel_break"), upper, L0[1], (pole if has_pole else hgt), start
    if dh > 0.15 * hgt and dl < -0.15 * hgt:
        yield "broadening", upper, (H0[1] + L0[1]) / 2, hgt, start

    # --- three lows / three highs ---
    if H2 is not None:
        lows3 = (L0[1], L1[1], L2[1])
        neck3 = max(H0[1], H1[1])
        h3 = neck3 - min(lows3)
        if h3 > 0 and max(lows3) - min(lows3) <= 0.2 * h3 and abs(H0[1] - H1[1]) <= 0.35 * h3 \
                and H2[1] > neck3 + 0.5 * h3:
            yield "triple_bottom", _flat(neck3), min(lows3), h3, L2[0]
        h_head = (H0[1] + H1[1]) / 2 - L1[1]
        if (L1[1] < min(L0[1], L2[1]) - 0.2 * h_head and abs(L0[1] - L2[1]) <= 0.35 * h_head
                and abs(H0[1] - H1[1]) <= 0.35 * h_head and H2[1] > neck3):
            yield "head_shoulders", upper, L0[1], h_head, L2[0]
        if H2[1] < H1[1] > H0[1] and L2[1] > L1[1] < L0[1]:
            yield "diamond", upper, L0[1], H1[1] - L1[1], H2[0]
        e1, e2, r1, r2 = L2[1] - L1[1], L1[1] - L0[1], H1[1] - L2[1], H0[1] - L1[1]
        if min(e1, e2, r1, r2) > 0 and all(0.13 <= e / r <= 0.75 for e, r in ((e1, r1), (e2, r2))) \
                and 0.6 <= e2 / e1 <= 1.6 and H1[1] < H2[1] and H0[1] < H1[1]:
            yield "three_drives", upper, L0[1], ("abs", H2[1]), L2[0]


def _harmonics(P):
    """Harmonic completions (point D) once the high C = P[-1] is confirmed: (name, D price, stop, target, start)."""
    C, B, A = P[-1], P[-2], P[-3]
    ab, bc = A[1] - B[1], C[1] - B[1]
    if ab > 0 and 0.55 <= bc / ab <= 0.85 and C[1] < A[1]:
        d = C[1] - ab
        yield "abcd", d, d - 0.3 * ab, d + 0.618 * (C[1] - d), A[0]
    if len(P) < 4:
        return
    X = P[-4]
    xa = A[1] - X[1]
    if xa <= 0 or ab <= 0:
        return
    b, cc = ab / xa, bc / ab
    if 0.36 <= cc <= 0.90:
        for name, lo, hi, d_ratio in (("gartley", 0.55, 0.68, 0.786), ("bat", 0.36, 0.52, 0.886),
                                      ("butterfly", 0.72, 0.85, 1.27), ("crab", 0.36, 0.64, 1.618)):
            if lo <= b <= hi:
                d = A[1] - d_ratio * xa
                stop = X[1] - 0.1 * xa if d_ratio < 1 else d - 0.2 * xa
                yield name, d, stop, d + 0.618 * (A[1] - d), X[0]
    if 0.36 <= b <= 0.64 and 1.13 <= (C[1] - X[1]) / xa <= 1.45:
        d = C[1] - 0.786 * (C[1] - X[1])
        yield "cypher", d, X[1] - 0.1 * xa, d + 0.618 * (C[1] - d), X[0]
    if 1.13 <= cc <= 1.65:
        d = C[1] - 0.886 * (C[1] - X[1])
        if d < B[1]:
            yield "shark", d, d - 0.2 * xa, d + 0.5 * (C[1] - d), X[0]


def _bull(o, h, l, c, atr, rsi, k):
    """All bullish pattern trades: (name, bar, stop, target, first bar)."""
    n, P = len(c), pivots(h, l, atr, k)
    for j in range(3, len(P)):
        known, conf = P[max(0, j - 6):j + 1], P[j][3]
        end = min(P[j + 1][3] if j + 1 < len(P) else n - 1, conf + MAX_WAIT, n - 1)
        if P[j][2] == -1:                                         # a low is confirmed: breakouts upwards
            for name, line, stop, height, first in _breakouts(known):
                for i in range(conf, end + 1):
                    if i - first > MAX_LEN:
                        break
                    if c[i] > line(i):
                        if c[i - 1] <= line(i - 1) and i - first >= 8:
                            tp = height[1] if isinstance(height, tuple) else line(i) + height
                            yield name, i, stop, tp, first
                        break
            # RSI divergence: a lower low in price on a higher RSI low, traded when the low is confirmed
            L0, L1 = known[-1], known[-3]
            if L0[1] < L1[1] and rsi[L0[0]] > rsi[L1[0]] + 3 and rsi[L1[0]] < 40 and L0[0] - L1[0] <= 60:
                yield "rsi_divergence", conf, L0[1], c[conf] + 2 * (c[conf] - L0[1]), L1[0]
            # break and retest of a level touched by two earlier highs
            top = max(known[-2][1], known[-4][1])
            if abs(known[-2][1] - known[-4][1]) <= 0.5 * atr[conf]:
                broke = None
                for i in range(conf, min(conf + MAX_WAIT, n - 1) + 1):
                    if broke is None:
                        if c[i] > top + 0.25 * atr[i]:
                            broke = i
                    elif c[i] < top - 0.5 * atr[i] or i - broke > 20:
                        break
                    elif l[i] <= top + 0.25 * atr[i] and c[i] > max(top, o[i]):
                        yield ("break_retest", i, min(l[i], top) - 0.5 * atr[i], c[i] + (top - L1[1]), known[-4][0])
                        break
        else:                                                     # a high is confirmed: completions below
            for name, d, stop, tp, first in _harmonics(known):
                for i in range(conf, end + 1):
                    if l[i] <= d:
                        if c[i] > stop and i - first <= MAX_LEN:
                            yield name, i, stop, tp, first
                        break
            Hb, Lw, Hm, Lf = known[-1], known[-2], known[-3], known[-4]
            if Lw[1] < Lf[1] and Hb[1] > Hm[1]:                   # Quasimodo: back to the left shoulder
                for i in range(conf, end + 1):
                    if l[i] <= Lf[1]:
                        if c[i] > Lw[1]:
                            yield "quasimodo", i, Lw[1], Hb[1], Lf[0]
                        break
            if Lw[1] < Lf[1] < Hb[1] < Hm[1]:                     # Wolfe wave: 1=Lf 2=Hm 3=Lw 4=Hb
                l13, l14, below = _line(Lf, Lw), _line(Lf, Hb), None
                for i in range(conf, end + 1):
                    if l[i] < l13(i):
                        below = l[i] if below is None else min(below, l[i])
                    if below is not None and c[i] > l13(i) and i > conf:
                        yield "wolfe_wave", i, below, l14(i + (i - Lf[0]) // 3), Lf[0]
                        break
            for i in range(conf, end + 1):                        # 2B: poke below the last low, close back above
                if l[i] < Lw[1]:
                    for q in range(i, min(i + 3, n)):
                        if c[q] > Lw[1]:
                            s = l[i:q + 1].min()
                            yield "false_break_2b", q, s, c[q] + 2 * (c[q] - s), Lw[0]
                            break
                    break

    # flags and pennants as day traders draw them: a sharp move (the pole), a short drift back, then a break
    for i in range(45, n):
        a = atr[i]
        if not a > 0 or c[i] <= h[i - 1]:
            continue
        p = i - 5 - int(np.argmax(h[i - 25:i - 4][::-1])) if i >= 25 else 0      # top of the pole, 5-25 bars ago
        if p < 12 or h[p] < h[max(0, p - 30):p + 1].max() or h[p] < h[p:i].max():
            continue
        base = l[p - 12:p + 1].min()
        pole, low = h[p] - base, l[p + 1:i].min()
        back = (h[p] - low) / pole if pole > 0 else 1
        if pole < 4 * a or not 0.1 <= back <= 0.5:
            continue
        m = i - p - 1
        first_r = h[p + 1:p + 1 + max(m // 2, 2)].max() - l[p + 1:p + 1 + max(m // 2, 2)].min()
        last_r = h[i - max(m // 2, 2):i].max() - l[i - max(m // 2, 2):i].min()
        slope = np.polyfit(np.arange(m), h[p + 1:i], 1)[0]
        if slope > 0.02 * a or c[i - 1] > h[i - 2]:
            continue
        yield ("pennant" if last_r < 0.6 * first_r else "flag"), i, low - 0.1 * a, c[i] + pole, p - 12

    # rounding bottom: 50 closes that fit a U, then a close above the left rim
    W = 50
    x = np.linspace(-1, 1, W)
    A = np.vstack([x ** 2, x, np.ones(W)]).T
    pinv = np.linalg.pinv(A)
    last = -W
    for i in range(W + 20, n):
        y = c[i - W:i]
        rim = y[:5].max()
        if c[i] > rim >= c[i - 1] and i - last > W // 2:
            q = pinv @ y
            res = y - A @ q
            r2 = 1 - (res @ res) / max(((y - y.mean()) ** 2).sum(), 1e-12)
            if q[0] > 0 and r2 > 0.6 and abs(-q[1] / (2 * q[0])) < 0.4:
                last = i
                yield "rounding", i, (y.min() + rim) / 2, c[i] + (rim - y.min()), i - W

    # candlesticks and bar patterns: entry next candle, target 2x the stop
    rng, body = h - l, np.abs(c - o)
    for i in range(25, n):
        a = atr[i]
        if not a > 0 or rng[i] <= 0:
            continue
        up_c = c[i] > o[i]
        s, hit = min(l[i], l[i - 1]) - 0.1 * a, []
        if c[i - 1] < c[i - 6] - a:                                # after a fall
            prev_dn = c[i - 1] < o[i - 1]
            if up_c and prev_dn and c[i] > o[i - 1] and o[i] <= c[i - 1] and body[i] > body[i - 1]:
                hit.append("engulfing")
            lw, uw = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
            if lw >= 2 * body[i] and lw >= 0.6 * rng[i] and rng[i] >= 0.8 * a:
                hit.append("doji_reversal" if body[i] <= 0.1 * rng[i] and uw <= 0.1 * rng[i] else "pin_bar")
            if prev_dn and up_c and body[i - 1] >= 0.5 * a and o[i] < c[i - 1] + 0.1 * a \
                    and (o[i - 1] + c[i - 1]) / 2 < c[i] < o[i - 1]:
                hit.append("piercing")
            if prev_dn and body[i - 1] >= 0.7 * a and max(o[i], c[i]) < o[i - 1] \
                    and min(o[i], c[i]) >= c[i - 1] and body[i] < 0.5 * body[i - 1]:
                hit.append("harami")
            if prev_dn and up_c and abs(l[i] - l[i - 1]) <= 0.05 * a and body[i] >= 0.4 * a:
                hit.append("tweezer")
            if up_c and l[i] < l[i - 1] and c[i] > h[i - 1]:
                hit.append("outside_bar")
        for name in hit:
            yield name, i, s, c[i] + 2 * (c[i] - s), i - 1
        if c[i - 2] < c[i - 7] - a:
            s3 = l[i - 2:i + 1].min() - 0.1 * a
            tp3 = c[i] + 2 * (c[i] - s3)
            p, q = i - 2, i - 1
            if c[p] < o[p] and body[p] >= 0.7 * a and body[q] <= 0.35 * a and up_c and c[i] > (o[p] + c[p]) / 2 \
                    and max(o[q], c[q]) <= c[p] + 0.2 * a:
                yield "star", i, s3, tp3, p
            if all(c[z] > o[z] and body[z] >= 0.5 * a for z in (p, q, i)) and c[p] < c[q] < c[i]:
                yield "three_soldiers", i, s3, tp3, p
            if c[p] < o[p] and c[q] > o[q] and c[q] > o[p] and o[q] <= c[p] + 0.1 * a and c[i] > c[q]:
                yield "three_outside", i, s3, tp3, p
            if c[p] < o[p] and body[p] >= 0.5 * a and c[q] > o[q] and c[q] < o[p] and o[q] >= c[p] and c[i] > o[p]:
                yield "three_inside", i, s3, tp3, p
        if up_c and body[i] >= 0.9 * rng[i] and body[i] >= 1.5 * a:
            yield "marubozu", i, l[i], c[i] + 2 * (c[i] - l[i]), i
        p = i - 1                                                  # break of a quiet bar, with the 20-bar drift
        if c[p] > c[i - 21] and c[i] > h[p]:
            tp2 = c[i] + 2 * (c[i] - l[p])
            if h[p] < h[p - 1] and l[p] > l[p - 1]:
                yield "inside_bar", i, l[p], tp2, p - 1
                if h[p - 1] < h[p - 2] and l[p - 1] > l[p - 2]:
                    yield "shark32", i, l[p], tp2, p - 2
            if rng[p] <= rng[p - 6:p + 1].min():
                yield "nr7", i, l[p], tp2, p - 6


def stats() -> dict:
    """{"pattern:timeframe": {n, win, avg}} from scripts/research_patterns.py (after costs, 2019-2026)."""
    try:
        return json.loads(Path(__file__).with_name("pattern_stats.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


COLUMNS = ["name", "side", "i", "entry", "sl", "tp", "hold", "atr", "first"]


def find(bars: pd.DataFrame, pivot_atr: float = 1.5, min_stop_atr: float = 0.5, max_stop_atr: float = 6.0,
         min_rr: float = 0.5) -> pd.DataFrame:
    """Every pattern trade in these candles: name, side, bar, entry (the close), stop, target, hold (bars)."""
    o, h, l, c = (bars[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    atr, rsi = atr_of(h, l, c), rsi_of(c)
    rows = []
    for side, sgn, arrs, r in (("buy", 1, (o, h, l, c), rsi), ("sell", -1, (-o, -l, -h, -c), 100 - rsi)):
        for name, i, sl, tp, first in _bull(*arrs, atr, r, pivot_atr):
            e, a = arrs[3][i], atr[i]
            risk = e - sl
            if not (min_stop_atr * a <= risk <= max_stop_atr * a) or tp - e < min_rr * risk:
                continue
            rows.append((name, side, i, sgn * e, sgn * sl, sgn * tp, int(np.clip(3 * (i - first), 10, 100)), a, first))
    return pd.DataFrame(rows, columns=COLUMNS).drop_duplicates(["name", "side", "i"])


def generate(bars: pd.DataFrame, symbol: str, params: dict, spec: SymbolSpec) -> list[Signal]:
    tf = pd.Timedelta(minutes=TIMEFRAME_MINUTES[params["timeframe"]])
    found = find(bars, params.get("pivot_atr", 1.5))
    found = found[found.name.isin(set(params.get("patterns") or ALL))].sort_values("i")
    out = []
    for r in found.to_dict("records"):
        label = LABELS[r["name"]][0 if r["side"] == "buy" else 1]
        created = bars.index[r["i"]] + tf
        try:
            out.append(Signal(
                strategy=NAME, symbol=symbol, side=r["side"], order_type="market", created=created,
                entry=round(r["entry"], spec.digits), sl=round(r["sl"], spec.digits), tp1=round(r["tp"], spec.digits),
                move_sl_to_be=False, close_by=created + r["hold"] * tf,
                reason=(f"{label} on the {params['timeframe']} chart, forming since {bars.index[r['first']]:%d %b}. "
                        f"The candle closed at {spec.fmt(r['entry'])}. Stop {spec.fmt(r['sl'])} is beyond the pattern; "
                        f"target {spec.fmt(r['tp'])} is the textbook target.")))
        except ValueError:                       # levels collapsed by rounding
            continue
    return out
