"""Cost and latency: what each judge and drafter call spent, from the response's
own usage block, and where it is reported (the eval and the ledger)."""

import argparse
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from evals.cases import CASES
from evals.run_eval import _report
from evals.run_eval import main as run_eval_main
from steward import cli
from steward.config import Config
from steward.detectors import Finding
from steward.judge import Draft, JudgeError, Verdict, judge
from steward.remedy import Action, Proposal, propose
from steward.usage import CallStats, call_stats, format_summary, summarize

URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,pack.db.orders,PROD)"
MODEL = "claude-opus-4-8"


def _usage(input_tokens=1200, output_tokens=300, cache_creation=0, cache_read=0, breakdown=None):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                           cache_creation_input_tokens=cache_creation,
                           cache_read_input_tokens=cache_read, cache_creation=breakdown)


def _response(parsed, usage=None, model=MODEL):
    return SimpleNamespace(parsed_output=parsed, usage=usage or _usage(), model=model)


def _finding(kind="tag_drift"):
    return Finding(kind=kind, urn=URN, entity_name="orders", evidence={})


VERDICT = Verdict(approved=True, rationale="supported", confidence=0.9)
PROPOSAL = Proposal(summary="x", actions=[Action("add_tags", {})])


# --------------------------------------------------------------------------
# the arithmetic
# --------------------------------------------------------------------------

def test_cost_prices_every_token_class_at_its_own_rate():
    stats = CallStats(model=MODEL, latency_s=1.0, input_tokens=10_000, output_tokens=2_000,
                      cache_write_5m_tokens=1_000, cache_write_1h_tokens=500, cache_read_tokens=3_000)
    # $5 in, $25 out, $0.50 cache read per million; writes at 1.25x and 2x the input rate
    expected = (10_000 * 5 + 1_000 * 5 * 1.25 + 500 * 5 * 2 + 3_000 * 0.50 + 2_000 * 25) / 1e6
    assert stats.cost_usd == pytest.approx(expected) == pytest.approx(0.11275)


def test_an_unpriced_model_has_no_cost_rather_than_a_guess():
    assert CallStats(model="some-future-model", latency_s=1.0, input_tokens=1).cost_usd is None


def test_call_stats_reads_the_ttl_breakdown_and_the_serving_model():
    breakdown = SimpleNamespace(ephemeral_5m_input_tokens=400, ephemeral_1h_input_tokens=100)
    resp = _response(VERDICT, _usage(cache_creation=500, cache_read=50, breakdown=breakdown),
                     model="claude-opus-5-5")
    stats = call_stats(resp, MODEL, latency_s=2.5)
    assert (stats.model, stats.cache_write_5m_tokens, stats.cache_write_1h_tokens,
            stats.cache_read_tokens) == ("claude-opus-5-5", 400, 100, 50)


@pytest.mark.parametrize("breakdown, expected", [
    (None, (700, 0)),                                                   # no breakdown: default TTL
    (SimpleNamespace(ephemeral_5m_input_tokens=0, ephemeral_1h_input_tokens=0), (700, 0)),
    (SimpleNamespace(ephemeral_5m_input_tokens=0, ephemeral_1h_input_tokens=300), (400, 300)),
    (SimpleNamespace(ephemeral_5m_input_tokens=0, ephemeral_1h_input_tokens=900), (0, 700)),  # clamped
])
def test_the_cache_write_total_is_authoritative_and_split_by_ttl(breakdown, expected):
    stats = call_stats(_response(VERDICT, _usage(cache_creation=700, breakdown=breakdown)), MODEL, 1.0)
    assert (stats.cache_write_5m_tokens, stats.cache_write_1h_tokens) == expected


@pytest.mark.parametrize("speed, tier", [("fast", None), (None, "priority"), (None, "batch")])
def test_another_speed_or_tier_is_unpriced_rather_than_mispriced(speed, tier):
    usage = _usage()
    usage.speed, usage.service_tier = speed, tier
    assert call_stats(_response(VERDICT, usage), MODEL, 1.0).cost_usd is None


def test_call_stats_tolerates_a_response_without_usage():
    stats = call_stats(Mock(spec=[]), MODEL, latency_s=0.5)
    assert (stats.model, stats.input_tokens, stats.output_tokens) == (MODEL, 0, 0)


def test_summary_uses_nearest_rank_percentiles_and_names_what_the_cost_leaves_out():
    stats = [CallStats(model=MODEL, latency_s=float(s), input_tokens=1_000, output_tokens=100,
                       cache_read_tokens=10) for s in range(1, 11)]
    stats.append(CallStats(model="some-future-model", latency_s=11.0))
    stats.append(CallStats(model=MODEL, latency_s=12.0, measured=False))
    s = summarize(stats)
    assert (s["calls"], s["unpriced_calls"], s["unmeasured_calls"]) == (12, 1, 1)
    assert (s["latency_p50_s"], s["latency_p95_s"]) == (6.0, 12.0)    # failed calls' wall time counts
    assert s["prompt_tokens"] == 10 * 1_010                           # uncached + cache tokens
    assert s["total_cost_usd"] == pytest.approx(10 * (1_000 * 5 + 10 * 0.5 + 100 * 25) / 1e6)
    line = format_summary(s)
    assert "1 unpriced" in line and "1 failed with no usage returned" in line


def test_an_empty_run_summarizes_to_zero_calls():
    assert summarize([]) == {"calls": 0} and format_summary({"calls": 0}) == "model calls      0"


# --------------------------------------------------------------------------
# recorded where the calls are made
# --------------------------------------------------------------------------

def test_judge_records_its_call_when_asked():
    client = Mock()
    client.messages.parse.return_value = _response(VERDICT)
    calls: list[CallStats] = []
    with patch("steward.judge.client", return_value=client):
        assert judge(_finding(), PROPOSAL, MODEL, record_usage=calls) == VERDICT
    assert len(calls) == 1 and (calls[0].input_tokens, calls[0].output_tokens) == (1200, 300)
    assert calls[0].latency_s >= 0


def test_an_unparseable_verdict_is_recorded_as_unmeasured():
    from pydantic import ValidationError
    try:
        Verdict.model_validate({"approved": "maybe"})
    except ValidationError as err:
        invalid = err
    client = Mock()
    client.messages.parse.side_effect = invalid
    calls: list[CallStats] = []
    with patch("steward.judge.client", return_value=client), pytest.raises(JudgeError):
        judge(_finding(), PROPOSAL, MODEL, record_usage=calls)
    assert len(calls) == 1 and not calls[0].measured and calls[0].cost_usd is None


def test_a_drafted_proposal_records_the_drafter_call():
    client = Mock()
    client.messages.parse.return_value = _response(Draft(description="Orders.", basis=["name"]),
                                                   _usage(input_tokens=800, output_tokens=120))
    calls: list[CallStats] = []
    with patch("steward.judge.client", return_value=client):
        propose(_finding("missing_description"), MODEL, calls)
    assert [(c.input_tokens, c.output_tokens) for c in calls] == [(800, 120)]


def test_the_ledger_records_what_each_decision_cost(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    client = Mock()
    client.messages.parse.return_value = _response(VERDICT)

    async def findings(*_):
        return [_finding()]

    args = argparse.Namespace(limit=10, kinds=["tag_drift"], max_findings=0, floor=0.7, apply=False)
    with patch("steward.cli.collect_findings", findings), \
            patch("steward.cli.propose", return_value=PROPOSAL), \
            patch("steward.judge.client", return_value=client):
        assert asyncio.run(cli.cmd_fix(Config(ledger_path=str(ledger)), args)) == 0
    decided = json.loads(ledger.read_text().splitlines()[0])
    assert decided["event"] == "decided"
    assert decided["usage"][0]["input_tokens"] == 1200
    assert decided["usage"][0]["cost_usd"] == pytest.approx((1200 * 5 + 300 * 25) / 1e6)


def _fix(tmp_path, findings, client, propose_patch=None):
    ledger = tmp_path / "ledger.jsonl"

    async def collect(*_):
        return findings

    args = argparse.Namespace(limit=10, kinds=["tag_drift"], max_findings=0, floor=0.7, apply=False)
    patches = [patch("steward.cli.collect_findings", collect), patch("steward.judge.client", return_value=client)]
    if propose_patch:
        patches.append(patch("steward.cli.propose", return_value=propose_patch))
    for p in patches:
        p.start()
    try:
        result = asyncio.run(cli.cmd_fix(Config(ledger_path=str(ledger)), args))
    finally:
        for p in patches:
            p.stop()
    return result, ledger


def _records(ledger):
    return [json.loads(line) for line in ledger.read_text().splitlines()]


def test_a_drafted_decision_records_both_its_calls(tmp_path):
    client = Mock()
    client.messages.parse.side_effect = [
        _response(Draft(description="Orders.", basis=["name"]), _usage(input_tokens=800, output_tokens=120)),
        _response(VERDICT)]
    assert _fix(tmp_path, [_finding("missing_description")], client)[0] == 0
    decided = _records(tmp_path / "ledger.jsonl")[0]
    assert [u["input_tokens"] for u in decided["usage"]] == [800, 1200]   # drafter, then judge


def test_an_api_error_is_recorded_and_the_run_total_still_prints(tmp_path, capsys):
    client = Mock()
    client.messages.parse.side_effect = [_response(VERDICT), ConnectionError("the API went away")]
    with pytest.raises(ConnectionError):
        _fix(tmp_path, [_finding(), _finding()], client, propose_patch=PROPOSAL)
    records = _records(tmp_path / "ledger.jsonl")
    assert [r["event"] for r in records] == ["decided", "judge_failed"]
    assert records[1]["usage"][0]["measured"] is False
    err = capsys.readouterr().err
    assert "model calls      2:" in err and "1 failed with no usage returned" in err


def test_an_interrupt_still_counts_the_calls_the_finding_had_made(tmp_path, capsys):
    client = Mock()
    client.messages.parse.side_effect = [
        _response(Draft(description="Orders.", basis=["name"])), KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        _fix(tmp_path, [_finding("missing_description")], client)
    assert "model calls      2:" in capsys.readouterr().err   # the paid drafter call, and the cut one


def test_a_failed_draft_records_its_usage_in_the_ledger(tmp_path):
    from pydantic import ValidationError
    try:
        Draft.model_validate({})
    except ValidationError as err:
        invalid = err
    client = Mock()
    client.messages.parse.side_effect = invalid
    assert _fix(tmp_path, [_finding("missing_description")], client)[0] == 0
    failed = _records(tmp_path / "ledger.jsonl")[0]
    assert failed["event"] == "propose_failed" and failed["usage"][0]["measured"] is False


def test_the_eval_records_each_judgement_s_own_usage(tmp_path, monkeypatch):
    client = Mock()
    client.messages.parse.return_value = _response(VERDICT)
    out = tmp_path / "results.jsonl"
    monkeypatch.setattr("sys.argv", ["run_eval", "--repeats", "2", "--only", CASES[0].name,
                                     "--out", str(out)])
    with patch("steward.judge.client", return_value=client):
        run_eval_main()
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(records) == 2 and all(len(r["usage"]) == 1 for r in records)


def test_the_eval_report_prints_cost_and_latency(capsys, tmp_path):
    results = [{"case": "c", "expected": True, "majority": True, "correct": True, "unanimous": True,
                "approvals": [True], "mean_confidence": 0.9}]
    usage = summarize([CallStats(model=MODEL, latency_s=3.0, input_tokens=1_000, output_tokens=200)])
    _report(results, 1, 3.0, tmp_path / "r.jsonl", usage)
    out = capsys.readouterr().out
    assert "model calls      1: $0.0100 total" in out and "p95 3.0s" in out
