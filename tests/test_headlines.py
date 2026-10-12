from forexbot.headlines import classify, parse_feed

W = ["XAUUSD", "EURUSD", "GBPUSD", "BTCUSD", "ETHUSD", "XRPUSD"]


def test_classify_markets_and_big():
    assert classify("FXStreet", "Gold surges to record high as Fed signals rate cut", W) == (["XAUUSD"], True)
    assert classify("CoinDesk", "Bitcoin ETF outflows deepen", W) == (["BTCUSD"], True)
    assert classify("FXStreet", "EUR/USD drifts ahead of ECB minutes", W) == (["EURUSD"], False)
    assert classify("FXStreet", "US CPI comes in hot at 0.5%", W) == (["USD"], True)
    assert classify("Cointelegraph", "New NFT marketplace launches", W) == ([], False)
    assert classify("Federal Reserve", "Minutes of the Federal Open Market Committee", W) == (["USD"], True)


def test_parse_feed_keeps_only_relevant_items():
    raw = b"""<rss><channel>
      <item><title>XRP jumps after SEC charges dropped</title><link>https://x/1</link>
            <pubDate>Thu, 08 Oct 2026 12:00:00 GMT</pubDate></item>
      <item><title>Celebrity buys a yacht</title><link>https://x/2</link></item>
    </channel></rss>"""
    items = parse_feed("Cointelegraph", raw, W)
    assert len(items) == 1 and items[0].markets == ["XRPUSD"]
    assert str(items[0].time) == "2026-10-08 12:00:00+00:00"
