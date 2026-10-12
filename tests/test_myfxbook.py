from forexbot import myfxbook as fxb

ROW = {"name": "EURUSD", "longPercentage": 74, "shortPercentage": 26, "longPositions": 2404,
       "shortPositions": 1018, "avgLongPrice": 1.13, "avgShortPrice": 1.11}


def test_record_and_latest_skip_small_crowds(tmp_path):
    path = tmp_path / "sentiment.csv"
    small = {**ROW, "name": "XRPUSD", "longPositions": 3, "shortPositions": 0}
    rows = fxb.record([ROW, small, {**ROW, "name": "AUDCAD"}], ["EURUSD", "XRPUSD"], path)
    assert [r["symbol"] for r in rows] == ["EURUSD", "XRPUSD"]
    got = fxb.latest(path)
    assert list(got) == ["EURUSD"]          # XRP crowd of 3 is ignored


def test_describe_flags_extremes_only():
    assert "leans hard to buying" in fxb.describe({"long_pct": 74, "short_pct": 26})
    assert "leans" not in fxb.describe({"long_pct": 55, "short_pct": 45})
