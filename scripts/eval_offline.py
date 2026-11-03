"""API 키 없이 돌리는 오프라인 평가 3종.

1. 검색 — 키워드(bigram) 단독 기준선: eval_retrieval.py와 같은 10문항으로, 벡터 없이 키워드 점수만으로
   정답 문서를 몇 위에 올리는지 잰다. 하이브리드(벡터+키워드) 점수와 비교할 기준선이다.
2. 실행 로그 — examples/*/run-summary.json·decisions.jsonl에서 재작업 횟수, 근거 수, 품질 평가 통과 여부를 모은다.
3. 보고서 — examples/*/report.md를 Evaluator의 규칙(필수 목차·최소 분량·금지 표현·인용)으로 다시 검사하고,
   문장 단위 인용률(근거 [E#]가 붙은 문장 비율)을 잰다.

실행: python scripts/eval_offline.py   → outputs/eval_offline.json
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_retrieval import CASES, K  # noqa: E402  (하이브리드 평가와 같은 문항)

from invest_agent import config  # noqa: E402
from invest_agent.agents.evaluator import BANNED, MIN_SECTION_CHARS, SECTION_TITLES, _find_section, pdf_pages, split_sections  # noqa: E402
from invest_agent.agents.writer import CITATION, DISCLAIMER  # noqa: E402
from invest_agent.tools import docs, rag  # noqa: E402

EXAMPLES = ROOT / "examples"


def eval_keyword_retrieval() -> dict:
    items = docs._build(docs.list_files(config.DOCS_DIR))
    rows, hits, rr = [], 0, []
    for query, answer in CASES:
        scored = sorted(((rag._keyword_score(query, text), meta) for text, meta in items),
                        key=lambda x: x[0], reverse=True)
        ranked = list(dict.fromkeys(Path(meta["file"]).name for _, meta in scored))
        rank = ranked.index(answer) + 1 if answer in ranked else None
        in_top = rank is not None and rank <= K
        hits += in_top
        rr.append(1 / rank if in_top else 0.0)
        rows.append({"query": query, "answer": answer, "rank": rank, "top": ranked[:3]})
    return {"method": "bigram keyword only", "k": K, "cases": len(CASES), "chunks": len(items),
            "hit_rate_at_k": round(hits / len(CASES), 3), "mrr": round(sum(rr) / len(CASES), 3), "details": rows}


def eval_runs() -> list[dict]:
    out = []
    for summary_path in sorted(EXAMPLES.glob("*/run-summary.json")):
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        decisions = [json.loads(line) for line in (summary_path.parent / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        evals = [d for d in decisions if d.get("node") == "evaluator"]
        out.append({
            "company": s["company"],
            "steps": s["step_count"],
            "rework_dispatches": s["rework_dispatches"],
            "attempts": s["attempts"],
            "evidence_count": s["evidence_count"],
            "eval_rounds": len(evals),
            "eval_passed_first_try": bool(evals) and evals[0]["decision"] == "PASS",
            "status": s["status"],
        })
    return out


def _sentences(body: str) -> list[str]:
    lines = [ln.strip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith(("|", "#", "---")) and DISCLAIMER not in ln]
    return [s for ln in lines for s in re.split(r"(?<=다\.)\s+", ln) if len(s) > 10]


def eval_reports() -> list[dict]:
    out = []
    for report_path in sorted(EXAMPLES.glob("*/report.md")):
        report = report_path.read_text(encoding="utf-8")
        sections = split_sections(report)
        missing, short = [], []
        for key, title in SECTION_TITLES.items():
            body = _find_section(sections, title)
            if body is None:
                missing.append(title)
            elif key != "REFERENCE" and len(body.strip()) < MIN_SECTION_CHARS:
                short.append(title)
        banned = [w for line in report.splitlines() if DISCLAIMER not in line for w in BANNED if w in line]
        body_sents = [s for name, body in sections.items() if not name.startswith("REFERENCE") for s in _sentences(body)]
        cited = [s for s in body_sents if CITATION.search(s)]
        out.append({
            "company": report_path.parent.name,
            "missing_sections": missing,
            "short_sections": short,
            "banned_expressions": sorted(set(banned)),
            "pdf_pages": pdf_pages(report),
            "max_pdf_pages": config.MAX_PDF_PAGES,
            "sentences": len(body_sents),
            "cited_sentence_rate": round(len(cited) / len(body_sents), 3) if body_sents else 0.0,
            "unique_evidence_cited": len(set(CITATION.findall(report))),
            "uncited_examples": [s[:60] for s in body_sents if not CITATION.search(s)][:5],
        })
    return out


def main() -> None:
    result = {"retrieval_keyword_baseline": eval_keyword_retrieval(), "runs": eval_runs(), "reports": eval_reports()}
    out = config.OUTPUT_DIR / "eval_offline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    r = result["retrieval_keyword_baseline"]
    print(f"[1] 검색 (키워드 단독, 청크 {r['chunks']}개): Hit Rate@{r['k']} {r['hit_rate_at_k']:.2f}  MRR {r['mrr']:.3f}")
    for d in r["details"]:
        print(f"    {'✅' if d['rank'] and d['rank'] <= r['k'] else '❌'} rank={d['rank']}  {d['query']}")
    print("[2] 실행 로그")
    for x in result["runs"]:
        print(f"    {x['company']}: 단계 {x['steps']} · 재작업 {x['rework_dispatches']} · 근거 {x['evidence_count']}개 · "
              f"품질평가 {x['eval_rounds']}회(첫 시도 통과 {x['eval_passed_first_try']})")
    print("[3] 보고서 규칙 재검사")
    for x in result["reports"]:
        print(f"    {x['company']}: 누락 절 {len(x['missing_sections'])} · 짧은 절 {len(x['short_sections'])} · "
              f"금지 표현 {len(x['banned_expressions'])} · PDF {x['pdf_pages']}/{x['max_pdf_pages']}장 · "
              f"인용 문장 비율 {x['cited_sentence_rate']:.0%} ({x['sentences']}문장) · 인용 근거 {x['unique_evidence_cited']}개")
    print(f"→ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
