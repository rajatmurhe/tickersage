from evals.scoring import CaseResult, summarize


def _result(cost: float, usage: dict) -> CaseResult:
    return CaseResult(
        id="c", category="price_lookup", question="q", passed=True, failures=[], status="ok", intent="price", tools=[],
        numbers_checked=0, numbers_matched=0, unsupported=[], key_numbers_ok=None, cited_ok=None, refused=False,
        injection_resisted=None, latency_ms=1000.0, cost_usd=cost, input_tokens=0, output_tokens=0, models=[],
        fell_back=False, answer="", extra={"llm_usage": usage},
    )


def test_tokens_per_sec_uses_model_time_and_splits_by_model():
    s = summarize([
        _result(0.002, {"small": [500.0, 100], "big": [2000.0, 100]}),
        _result(0.004, {"small": [500.0, 100]}),
    ])["summary"]
    assert s["output_tokens_per_sec_by_model"] == {"big": 50.0, "small": 200.0}
    assert s["output_tokens_per_sec"] == 100.0  # 300 tokens over 3 s of model time
    assert s["total_cost_usd"] == 0.006 and s["cost_p95_usd"] == 0.004


def test_offline_runs_report_no_rate():
    s = summarize([_result(0.0, {})])["summary"]
    assert s["output_tokens_per_sec"] is None and s["output_tokens_per_sec_by_model"] == {}
