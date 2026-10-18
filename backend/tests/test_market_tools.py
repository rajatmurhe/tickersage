import httpx
import pytest
import respx

from app.cache import MemoryCache
from app.config import Settings
from app.embeddings import HashingEmbedder
from app.schemas import ToolCall
from app.symbols import SymbolRegistry
from app.tools.docs import DocsIndex
from app.tools.market import MarketData
from app.tools.news import NewsClient
from app.tools.registry import RunOptions, Toolbox

BINANCE = "https://api.binance.com"
MIRROR = "https://data-api.binance.vision"
CG = "https://api.coingecko.com/api/v3"

TICKER = {"symbol": "BTCUSDT", "priceChange": "-204.39000000", "priceChangePercent": "-0.251",
          "lastPrice": "81143.80000000", "openPrice": "81348.19000000", "highPrice": "82295.47000000",
          "lowPrice": "80819.66000000", "volume": "8908.76287000", "quoteVolume": "727012753.67976480",
          "closeTime": 1792338944001}


def settings() -> Settings:
    return Settings(llm_mode="offline", tool_timeout_s=0.5, tool_retries=1, database_url="sqlite://")


@pytest.fixture
async def toolbox():
    s = settings()
    async with httpx.AsyncClient() as http:
        reg = SymbolRegistry()
        market = MarketData(s, http, reg)
        docs = DocsIndex(s.docs_dir, HashingEmbedder())
        yield Toolbox(market=market, news=NewsClient(s, http), docs=docs, registry=reg, cache=MemoryCache())


@respx.mock
async def test_ticker_parses_and_rounds(toolbox):
    respx.get(f"{BINANCE}/api/v3/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKER))
    r = await toolbox.call(ToolCall(tool="get_ticker", args={"symbol": "bitcoin"}), 1)
    assert r.ok and r.args == {"symbol": "BTCUSDT"}
    assert r.data["last_price"] == 81143.8 and r.data["price_change_pct"] == -0.25
    assert r.data["volume_quote"] == 727012753.68 and r.source == "Binance public API"


@respx.mock
async def test_ticker_is_cached(toolbox):
    route = respx.get(f"{BINANCE}/api/v3/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKER))
    first = await toolbox.call(ToolCall(tool="get_ticker", args={"symbol": "BTC"}), 1)
    second = await toolbox.call(ToolCall(tool="get_ticker", args={"symbol": "btcusdt"}), 2)
    assert not first.cached and second.cached and route.call_count == 1


@respx.mock
async def test_retries_then_falls_back_to_mirror(toolbox):
    primary = respx.get(f"{BINANCE}/api/v3/ticker/24hr").mock(return_value=httpx.Response(503))
    respx.get(f"{MIRROR}/api/v3/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKER))
    r = await toolbox.call(ToolCall(tool="get_ticker", args={"symbol": "BTC"}), 1)
    assert r.ok and r.source == "Binance public API (mirror)"
    assert primary.call_count == 2  # one try + one retry


@respx.mock
async def test_falls_back_to_coingecko_when_binance_is_down(toolbox):
    respx.get(f"{BINANCE}/api/v3/ticker/24hr").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{MIRROR}/api/v3/ticker/24hr").mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get(f"{CG}/coins/markets").mock(return_value=httpx.Response(200, json=[{
        "current_price": 81050, "price_change_24h": -226.26, "price_change_percentage_24h": -0.24481,
        "high_24h": 82178, "low_24h": 80823, "total_volume": 17763979794, "last_updated": "2026-10-18T16:00:00Z"}]))
    r = await toolbox.call(ToolCall(tool="get_ticker", args={"symbol": "BTC"}), 1)
    assert r.ok and r.source == "CoinGecko API (backup)"
    assert r.data["last_price"] == 81050 and "aggregated" in r.data["volume_note"]


@respx.mock
async def test_timeout_without_backup_is_a_clean_error(toolbox):
    toolbox.registry.load([{"symbol": "PEPEUSDT", "baseAsset": "PEPE", "quoteAsset": "USDT"},
                           {"symbol": "OBSCUREUSDT", "baseAsset": "OBSCURE", "quoteAsset": "USDT"}])
    toolbox.registry.loaded = True
    respx.get(url__regex=r".*/ticker/24hr.*").mock(side_effect=httpx.ReadTimeout("slow"))
    r = await toolbox.call(ToolCall(tool="get_ticker", args={"symbol": "OBSCURE"}), 1)
    assert not r.ok and r.error.code == "timeout" and r.data is None


async def test_unknown_symbol_is_rejected_before_any_request(toolbox):
    with respx.mock(assert_all_called=False) as mock:
        r = await toolbox.call(ToolCall(tool="get_ticker", args={"symbol": "FAKECOIN"}), 1)
        assert mock.calls.call_count == 0
    assert r.error.code == "unknown_symbol" and "FAKECOIN" in r.error.message


async def test_invalid_input_is_reported(toolbox):
    r = await toolbox.call(ToolCall(tool="get_klines", args={"symbol": "BTC", "interval": "3w"}), 1)
    assert r.error.code == "invalid_input"


async def test_fault_injection(toolbox):
    r = await toolbox.call(ToolCall(tool="get_ticker", args={"symbol": "BTC"}), 1, RunOptions(faults={"get_ticker": "timeout"}))
    assert r.error.code == "timeout"


@respx.mock
async def test_order_book_metrics(toolbox):
    respx.get(f"{BINANCE}/api/v3/depth").mock(return_value=httpx.Response(200, json={
        "bids": [["100.0", "2"], ["99.5", "4"], ["90.0", "100"]],
        "asks": [["100.2", "1"], ["100.9", "3"], ["110.0", "100"]]}))
    r = await toolbox.call(ToolCall(tool="get_order_book", args={"symbol": "ETH", "depth": 5}), 1)
    d = r.data
    assert d["best_bid"] == 100.0 and d["best_ask"] == 100.2 and d["mid_price"] == 100.1
    assert d["spread_bps"] == pytest.approx(19.98, abs=0.01)
    assert d["bid_depth_quote_1pct"] == 598.0  # 100*2 + 99.5*4; the 90 level is outside 1%
    assert d["ask_depth_quote_1pct"] == 402.9  # 100.2*1 + 100.9*3


def test_symbol_extraction():
    reg = SymbolRegistry()
    assert reg.extract("Compare ETH and SOL") == ["ETH", "SOL"]
    assert reg.extract("price of bitcoin vs ethereum") == ["BTC", "ETH"]
    assert reg.extract("BTC/USDT last price") == ["BTC"]
    assert reg.extract("sell 100 USDT worth of DOGE") == ["DOGE"]
    assert reg.extract("What's FAKECOIN trading at?") == ["FAKECOIN"]
    assert reg.extract("What is the RSI in UTC terms?") == []
    assert reg.resolve("eth") == "ETHUSDT" and reg.resolve("BTC/USDT") == "BTCUSDT"
