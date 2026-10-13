"""OpenDART API 클라이언트.

- 기업 고유번호(corp_code) 목록은 최초 1회 내려받아 .cache/ 에 저장한다.
- 상장사뿐 아니라 감사보고서를 공시하는 비상장 외부감사 대상 기업도 검색한다.
"""

import io
import json
import re
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

BASE_URL = "https://opendart.fss.or.kr/api"
CACHE_DIR = Path(os.getenv("INVEST_AGENT_CACHE_DIR", Path(__file__).resolve().parents[2] / ".cache"))
CORP_CACHE = CACHE_DIR / "corp_codes_all.json"   # 상장·비상장 전체 (약 12만 개)
CORP_CACHE_TTL = 7 * 24 * 3600

MARKETS = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "기타"}


class DartError(Exception):
    pass


@dataclass(frozen=True)
class Corp:
    corp_code: str
    name: str
    stock_code: str


def _api_key() -> str:
    key = os.getenv("OPENDART_API_KEY", "").strip()
    if not key:
        raise DartError(".env에 OPENDART_API_KEY가 설정되어 있지 않습니다.")
    return key


def _request(path: str, **params) -> bytes:
    params["crtfc_key"] = _api_key()
    url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _get_json(path: str, **params) -> dict:
    data = json.loads(_request(path, **params))
    status = data.get("status")
    if status == "013":  # 조회된 데이터 없음
        return {"list": []}
    if status != "000":
        raise DartError(f"DART 오류 {status}: {data.get('message')}")
    return data


# ---------------------------------------------------------------------------
# 기업 검색
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _all_corps() -> list[Corp]:
    """DART에 등록된 전체 기업 목록 (비상장 외부감사 대상 스타트업 포함)."""
    if CORP_CACHE.exists() and time.time() - CORP_CACHE.stat().st_mtime < CORP_CACHE_TTL:
        rows = json.loads(CORP_CACHE.read_text(encoding="utf-8"))
    else:
        zf = zipfile.ZipFile(io.BytesIO(_request("corpCode.xml")))
        root = ET.fromstring(zf.read(zf.namelist()[0]))
        rows = [
            {"corp_code": e.findtext("corp_code"), "name": e.findtext("corp_name"),
             "stock_code": (e.findtext("stock_code") or "").strip()}
            for e in root.iter("list")
        ]
        CACHE_DIR.mkdir(exist_ok=True)
        CORP_CACHE.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return [Corp(**r) for r in rows]


def _normalize(name: str) -> str:
    return name.replace(" ", "").replace("(주)", "").replace("주식회사", "").lower()


def search_corps(query: str, limit: int = 10) -> list[Corp]:
    """회사명 또는 6자리 종목코드로 기업을 검색 (비상장 포함). 정확히 일치하는 항목이 맨 앞."""
    q = query.strip()
    code = re.search(r"\b(\d{6})\b", q)  # '현대자동차(005380)' 같은 입력 처리
    if code:
        q = code.group(1)
    corps = _all_corps()
    if q.isdigit():
        return [c for c in corps if c.stock_code == q.zfill(6)]
    nq = _normalize(q)
    exact = [c for c in corps if _normalize(c.name) == nq]
    partial = [c for c in corps if nq in _normalize(c.name) and c not in exact]
    partial.sort(key=lambda c: (len(c.name), not c.stock_code))
    return (exact + partial)[:limit]


def resolve_corp(query: str) -> Corp:
    """정확히 한 기업으로 확정. 애매하면 후보를 담아 예외를 던진다."""
    found = search_corps(query)
    if not found:
        raise DartError(f"'{query}'에 해당하는 DART 등록 기업을 찾을 수 없습니다. 정식 회사명을 사용하세요.")
    if re.search(r"\b\d{6}\b", query) or _normalize(found[0].name) == _normalize(query):
        return found[0]
    cands = ", ".join(f"{c.name}({c.stock_code or '비상장'})" for c in found[:8])
    raise DartError(f"'{query}'와 정확히 일치하는 회사가 없습니다. 후보: {cands}. "
                    "의도한 회사가 후보에 없으면 정식 회사명이나 종목코드로 다시 시도하세요.")


# ---------------------------------------------------------------------------
# 데이터 조회
# ---------------------------------------------------------------------------

@lru_cache(maxsize=128)
def company_info(corp_code: str) -> dict:
    return _get_json("company.json", corp_code=corp_code)


def recent_disclosures(corp_code: str, days: int = 90, limit: int = 30) -> list[dict]:
    end = date.today()
    bgn = date.fromordinal(end.toordinal() - days)
    data = _get_json("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"),
                     end_de=end.strftime("%Y%m%d"), page_count=str(min(limit, 100)))
    return data.get("list", [])
