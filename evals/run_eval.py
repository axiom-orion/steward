"""Measure the gate, not the model.

Two questions, because they fail differently:

  correctness  On a labeled set, does the judge approve what should be applied
               and refuse what should not? Refusals are weighted separately —
               a missed refusal writes wrong metadata into a catalog people
               trust, while a wrong refusal only leaves a gap someone can fix.

  consistency  Asked the identical question N times, does it answer the same
               way? A gate that flips is not a gate, and this cannot be read
               off a single run. Reported per case, so an unstable case is
               named rather than averaged away.

    python -m evals.run_eval --repeats 3

Requires ANTHROPIC_API_KEY. No DataHub instance — the corpus is frozen in
evals/fixtures/.
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from steward.config import Config

from .cases import CASES, load_snapshot


def _note(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_eval")
    parser.add_argument("--repeats", type=int, default=3,
                        help="judgements per case; >1 measures consistency")
    parser.add_argument("--out", default="evals/results.jsonl")
    parser.add_argument("--only", default="", help="comma-separated case names")
    args = parser.parse_args()

    from steward.judge import JudgeError, judge      # late import: keeps --help key-free

    cfg = Config()
    snapshot = load_snapshot()
    cases = [c for c in CASES if not args.only or c.name in args.only.split(",")]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    started = time.time()

    with out_path.open("w", encoding="utf-8") as sink:
        for case in cases:
            finding, proposal = case.build(snapshot)
            verdicts = []
            errors = 0
            for i in range(args.repeats):
                try:
                    verdict = judge(finding, proposal, cfg.model)
                except JudgeError as err:
                    # Not a verdict — excluded from accuracy and counted openly,
                    # rather than folded in as a refusal it never issued.
                    errors += 1
                    sink.write(json.dumps({"case": case.name, "run": i, "error": str(err)[:400]}) + "\n")
                    sink.flush()
                    _note(f"     judge error on {case.name} run {i}: {str(err)[:120]}")
                    continue
                verdicts.append(verdict)
                record = {
                    "case": case.name,
                    "run": i,
                    "expected_approved": case.expected_approved,
                    "approved": verdict.approved,
                    "correct": verdict.approved == case.expected_approved,
                    "confidence": verdict.confidence,
                    "rationale": verdict.rationale,
                    "kind": finding.kind,
                    "proposal": proposal.summary,
                    "synthetic": case.synthetic,
                }
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()

            if not verdicts:
                _note(f"[ERR ] {case.name:22s} no usable verdict in {args.repeats} attempt(s)")
                results.append({
                    "case": case.name, "expected": case.expected_approved, "majority": None,
                    "correct": False, "unanimous": False, "approvals": [], "errors": errors,
                    "mean_confidence": 0.0, "synthetic": case.synthetic, "why": case.why,
                })
                continue

            approvals = [v.approved for v in verdicts]
            majority = sum(approvals) > len(approvals) / 2
            results.append({
                "errors": errors,
                "case": case.name,
                "expected": case.expected_approved,
                "majority": majority,
                "correct": majority == case.expected_approved,
                "unanimous": len(set(approvals)) == 1,
                "approvals": approvals,
                "mean_confidence": statistics.mean(v.confidence for v in verdicts),
                "synthetic": case.synthetic,
                "why": case.why,
            })
            flag = "ok " if results[-1]["correct"] else "MISS"
            stability = "" if results[-1]["unanimous"] else "  <- FLIPPED"
            _note(f"[{flag}] {case.name:22s} expected={'approve' if case.expected_approved else 'reject ':>7} "
                  f"got={approvals}{stability}")

    _report(results, args.repeats, time.time() - started, out_path)
    return 0 if all(r["correct"] for r in results) else 1


def _report(results: list[dict], repeats: int, elapsed: float, out_path: Path) -> None:
    total = len(results)
    correct = sum(r["correct"] for r in results)
    unanimous = sum(r["unanimous"] for r in results)

    # Refusals are the safety-relevant class: treat "should reject" as positive.
    should_reject = [r for r in results if not r["expected"]]
    caught = [r for r in should_reject if not r["majority"]]
    should_approve = [r for r in results if r["expected"]]
    approved = [r for r in should_approve if r["majority"]]

    print()
    print(f"cases            {total}  ({repeats} judgement(s) each, {elapsed:.0f}s)")
    print(f"accuracy         {correct}/{total}")
    print(f"unanimous        {unanimous}/{total}" + ("" if unanimous == total else "   <- the rest flipped between runs"))
    print(f"bad writes caught {len(caught)}/{len(should_reject)}   (refusals the gate exists for)")
    print(f"good writes kept  {len(approved)}/{len(should_approve)}   (fixes it should not block)")

    if len(should_reject):
        missed = [r["case"] for r in should_reject if r["majority"]]
        if missed:
            print(f"\nLET THROUGH (would have written bad metadata): {missed}")
    blocked = [r["case"] for r in should_approve if not r["majority"]]
    if blocked:
        print(f"BLOCKED (safe, but leaves the gap unfixed): {blocked}")
    flippy = [(r["case"], r["approvals"]) for r in results if not r["unanimous"]]
    if flippy:
        print("\nUNSTABLE — same input, different answers:")
        for name, approvals in flippy:
            print(f"  {name}: {approvals}")

    conf_correct = [r["mean_confidence"] for r in results if r["correct"]]
    conf_wrong = [r["mean_confidence"] for r in results if not r["correct"]]
    if conf_wrong:
        print(f"\nmean confidence when right {statistics.mean(conf_correct):.2f} / "
              f"when wrong {statistics.mean(conf_wrong):.2f}"
              "   (if these are close, confidence is not a usable signal)")
    print(f"\nper-judgement records: {out_path}")


if __name__ == "__main__":
    sys.exit(main())
