import pytest

from app.numbers import extract_numbers, fmt_big, fmt_price, matches_value, round_price
from app.schemas import ToolResult
from app.verifier import verify


def ticker_result(**data) -> ToolResult:
    base = {"symbol": "BTCUSDT", "base_asset": "BTC", "last_price": 81113.66, "price_change": -296.72,
            "price_change_pct": -0.36, "high_24h": 82295.47, "low_24h": 80819.66, "volume_quote": 727768495.71,
            "as_of": "2026-10-18T16:09:44+00:00"}
    base.update(data)
    return ToolResult(id=1, tool="get_ticker", args={"symbol": "BTCUSDT"}, ok=True, data=base)


@pytest.mark.parametrize(
    "text,value,mult,pct",
    [
        ("$81,113.66", 81113.66, 1, False),
        ("2.5%", 2.5, 1, True),
        ("$727.77M", 727.77, 1e6, False),
        ("1.2 billion", 1.2, 1e9, False),
        ("3.07x", 3.07, 1, False),
        ("$0.00001234", 0.00001234, 1, False),
    ],
)
def test_extract_numbers(text, value, mult, pct):
    (n,) = extract_numbers(text)
    assert n.value == pytest.approx(value) and n.multiplier == mult and n.is_pct is pct


@pytest.mark.parametrize(
    "text",
    ["Data as of 16:09 UTC on 2026-10-18", "over the last 24 hours", "a 7-day window", "RSI(14) is",
     "RSI 14 reading", "20-period SMA", "see [1] and [2, 3]", "on Oct 18, 2026", "Explode in October 2026",
     "1. first item"],
)
def test_exempt_spans_produce_no_numbers(text):
    assert extract_numbers(text) == []


def test_tickers_with_digits_are_not_numbers():
    assert extract_numbers("ETH2 and 1INCH") == []


@pytest.mark.parametrize(
    "written,target,ok",
    [
        ("$81,114", 81113.66, True),       # rounded
        ("$81,113", 81113.66, True),       # truncated
        ("$81.1K", 81113.66, True),
        ("$727.8M", 727768495.71, True),
        ("0.36%", -0.36, True),            # sign is carried by words ("down")
        ("0.4%", -0.36, True),
        ("0.5%", -0.36, False),
        ("$82,000", 81113.66, False),
        ("36%", 0.36, True),               # fraction written as percent
    ],
)
def test_matches_value(written, target, ok):
    (n,) = extract_numbers(written)
    assert matches_value(n, target) is ok


def test_verify_accepts_faithful_answer():
    answer = "BTC is at $81,113.66, down 0.36% ($296.72) in 24 hours [1], with $727.77M traded."
    report = verify(answer, [ticker_result()])
    assert report.checked == 4 and report.unsupported == [] and report.faithfulness == 1.0


def test_verify_flags_hallucinated_numbers():
    answer = "BTC is at $81,113.66 and analysts expect $95,000 soon, a 17% upside."
    report = verify(answer, [ticker_result()])
    assert report.unsupported == ["$95,000", "17%"]
    assert report.faithfulness == pytest.approx(1 / 3)


def test_verify_allows_numbers_from_question_and_tool_text():
    docs = ToolResult(id=2, tool="search_docs", args={"query": "q"}, ok=True,
                      data={"chunks": [{"id": "x#0", "title": "Funding", "url": "u",
                                        "text": "Many exchanges settle funding every 8 hours; 0.01% is typical."}]})
    report = verify("Funding of 0.01% is common; you asked about 50,000 USDT.", [docs], "What about 50,000 USDT?")
    assert report.unsupported == []


def test_small_counting_numbers_are_not_checked():
    assert verify("3 of the 5 headlines were negative.", [ticker_result()]).checked == 0


def test_failed_tools_contribute_no_numbers():
    failed = ToolResult(id=1, tool="get_ticker", args={}, ok=False, data=None)
    assert verify("BTC is $81,113.66", [failed]).unsupported == ["$81,113.66"]


def test_formatting_round_trips_through_verifier():
    for value in (81113.66, 0.07769, 0.00001234, 2372.95):
        text = f"price {fmt_price(round_price(value))}"
        assert verify(text, [ticker_result(last_price=round_price(value))]).unsupported == []
    assert verify(f"volume {fmt_big(727768495.71)}", [ticker_result()]).unsupported == []


def test_typographic_dashes_do_not_create_unsupported_numbers():
    from app.numbers import extract_numbers
    # Nemotron writes non-breaking hyphens and minus signs; window lengths stay exempt, figures still parse.
    nums = extract_numbers("TRX fell \u221214.6\u202f% over the past 24\u2011hour window and 7\u2011day range.")
    assert [(n.value, n.is_pct) for n in nums] == [(14.6, True)]
