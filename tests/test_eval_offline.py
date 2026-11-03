"""오프라인 평가 회귀 테스트 (API 키 불필요) — 예시 보고서 품질과 키워드 검색 기준선이 떨어지지 않게 지킨다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eval_offline import eval_keyword_retrieval, eval_reports, eval_runs  # noqa: E402


def test_example_reports_pass_rule_checks():
    for r in eval_reports():
        assert not r["missing_sections"], r["company"]
        assert not r["short_sections"], r["company"]
        assert not r["banned_expressions"], r["company"]
        assert r["cited_sentence_rate"] >= 0.85, r["company"]
        if r["pdf_pages"] is not None:
            assert r["pdf_pages"] <= r["max_pdf_pages"], r["company"]


def test_example_runs_finished_and_passed_eval():
    runs = eval_runs()
    assert runs
    for r in runs:
        assert r["status"] == "completed" and r["eval_passed_first_try"], r["company"]


def test_keyword_retrieval_baseline():
    r = eval_keyword_retrieval()
    assert r["hit_rate_at_k"] >= 0.9 and r["mrr"] >= 0.9
