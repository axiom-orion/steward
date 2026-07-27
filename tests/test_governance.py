"""The two properties that must hold even when the judge is wrong:
nothing is written without an approving verdict, and a judge that fails to
answer blocks the write rather than being read as either answer."""

import json
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from evals.audit_ledger import audit
from steward.corpus import Dataset
from steward.detectors import Finding
from steward.judge import JudgeError, Verdict, judge
from steward.remedy import Action, Proposal

URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,pack.db.orders,PROD)"
ACTIONS = [{"tool": "add_tags", "args": {"tag_urns": ["urn:li:tag:pack.PII_Data"], "entity_urns": [URN]}}]
OTHER_ACTIONS = [{"tool": "set_domains", "args": {"domain_urn": "urn:li:domain:x", "entity_urns": [URN]}}]


def _ledger(tmp_path, *records):
    path = tmp_path / "ledger.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _decided(approved, actions=ACTIONS):
    return {"event": "decided", "finding": {"kind": "tag_drift", "urn": URN},
            "proposal": {"actions": actions}, "verdict": {"approved": approved}}


def _applied(actions=ACTIONS):
    return {"event": "applied", "finding": {"kind": "tag_drift", "urn": URN},
            "proposal": {"actions": actions}}


class TestLedgerAudit:
    def test_clean_ledger_passes(self, tmp_path):
        violations, summary = audit(_ledger(tmp_path, _decided(True), _applied()))
        assert violations == []
        assert summary["applied"] == 1 and summary["approved"] == 1

    def test_catches_write_with_no_verdict(self, tmp_path):
        violations, _ = audit(_ledger(tmp_path, _applied()))
        assert len(violations) == 1
        assert "NO VERDICT" in violations[0]

    def test_catches_write_the_judge_refused(self, tmp_path):
        violations, _ = audit(_ledger(tmp_path, _decided(False), _applied()))
        assert len(violations) == 1
        assert "REFUSED" in violations[0]

    def test_catches_a_swapped_write(self, tmp_path):
        """Approval of one write must not vouch for a different write on the
        same dataset."""
        violations, _ = audit(_ledger(tmp_path, _decided(True), _applied(OTHER_ACTIONS)))
        assert len(violations) == 1
        assert "DIFFERENT WRITE" in violations[0]


class TestJudgeFailsClosed:
    def test_unparseable_verdict_raises_rather_than_returning_one(self):
        try:
            Verdict.model_validate_json('{"approved": true, "rationale": "cut off mid-str')
        except ValidationError as real_error:
            truncated = real_error

        finding = Finding(kind="tag_drift", urn=URN, entity_name="orders", evidence={})
        proposal = Proposal(summary="x", actions=[Action("add_tags", {})])

        mock_client = Mock()
        mock_client.messages.parse.side_effect = truncated
        with patch("steward.judge.client", return_value=mock_client):
            with pytest.raises(JudgeError):
                judge(finding, proposal, "some-model")

    def test_a_verdict_that_parses_is_returned_unchanged(self):
        finding = Finding(kind="tag_drift", urn=URN, entity_name="orders", evidence={})
        proposal = Proposal(summary="x", actions=[Action("add_tags", {})])

        mock_client = Mock()
        mock_client.messages.parse.return_value = Mock(
            parsed_output=Verdict(approved=False, rationale="no", confidence=0.9)
        )
        with patch("steward.judge.client", return_value=mock_client):
            verdict = judge(finding, proposal, "some-model")
        assert verdict.approved is False and verdict.confidence == 0.9
