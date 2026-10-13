"""공용 RAG 계층: DART XML → 텍스트 변환, 청크 분할, Chroma + 한국어 bigram 하이브리드 검색.

- 감사보고서 주석(audit.py)과 문서 풀(docs.py)이 같은 검색 함수(hybrid_search)를 쓴다.
- 컬렉션 이름에 원문 식별자(접수번호·파일 해시)를 넣어, 원문이 바뀌면 자동으로 새 인덱스를 만든다.
"""

import html
import io
import re
import threading
import zipfile
from functools import lru_cache

import chromadb
from langchain_openai import OpenAIEmbeddings

from . import dart

CHROMA_DIR = dart.CACHE_DIR / "chroma"
EMBED_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


_index_lock = threading.Lock()
_client_lock = threading.Lock()
_client_instance: chromadb.ClientAPI | None = None


def _client() -> chromadb.ClientAPI:
    """Chroma 클라이언트는 프로세스당 하나만 만든다.

    ToolNode는 한 턴의 도구 호출을 여러 스레드에서 동시에 실행한다. 첫 호출 때 여러 스레드가
    동시에 PersistentClient를 만들면 'Could not connect to tenant default_tenant' 같은 초기화
    경합 오류가 나므로, 생성 구간을 잠금으로 보호한다.
    """
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                CHROMA_DIR.mkdir(parents=True, exist_ok=True)
                _client_instance = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client_instance


@lru_cache(maxsize=1)
def _embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=EMBED_MODEL, check_embedding_ctx_length=False)  # 청크가 짧아 토큰 분할 불필요 (tiktoken 다운로드 생략)


# ---------------------------------------------------------------------------
# 문서 파싱
# ---------------------------------------------------------------------------

def _strip(fragment: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"[\s　\xa0]+", " ", s).strip()


def _table_to_text(m: re.Match) -> str:
    """표의 각 행을 '셀 | 셀 | 셀' 한 줄로 변환."""
    rows = []
    for tr in re.findall(r"<TR[^>]*>(.*?)</TR>", m.group(0), re.S):
        cells = [_strip(c) for c in re.findall(r"<(?:TD|TH|TE|TU)[^>]*>(.*?)</(?:TD|TH|TE|TU)>", tr, re.S)]
        cells = [c for c in cells if c]
        if cells:
            rows.append(" | ".join(cells))
    return "\n" + "\n".join(rows) + "\n"


def _to_text(fragment: str) -> str:
    """DART XML 조각을 텍스트로 변환. 표는 행 단위로 유지."""
    s = re.sub(r"\s+", " ", fragment)  # 원본 줄바꿈 제거 후 구조 태그로 줄을 다시 나눈다
    s = re.sub(r"<TABLE[^>]*>.*?</TABLE>", _table_to_text, s, flags=re.S)
    s = re.sub(r"</P>|<BR\s*/?>|<P[^>]*>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    lines = (re.sub(r"[ \t　\xa0]+", " ", line).strip() for line in s.splitlines())
    return "\n".join(line for line in lines if line)


def _chunk(text: str) -> list[str]:
    chunks, buf = [], ""
    for line in text.splitlines():
        while len(line) > CHUNK_SIZE:  # 아주 긴 줄은 강제로 자름
            chunks.append(line[:CHUNK_SIZE])
            line = line[CHUNK_SIZE - CHUNK_OVERLAP:]
        if len(buf) + len(line) + 1 > CHUNK_SIZE and buf:
            chunks.append(buf)
            buf = buf[-CHUNK_OVERLAP:]
        buf = f"{buf}\n{line}" if buf else line
    if buf.strip():
        chunks.append(buf)
    return chunks


def _download_report(rcept_no: str) -> str:
    data = dart._request("document.xml", rcept_no=rcept_no)
    if not zipfile.is_zipfile(io.BytesIO(data)):
        # 파일 없음·요청 한도 초과 등은 zip 대신 <status>/<message> 응답이 온다
        body = data.decode("utf-8", "ignore")
        status = re.search(r"<status>(.*?)</status>", body)
        message = re.search(r"<message>(.*?)</message>", body)
        raise dart.DartError(
            f"사업보고서 원문을 받지 못했습니다 (DART {status.group(1) if status else '?'}: "
            f"{message.group(1) if message else body[:100]})"
        )
    zf = zipfile.ZipFile(io.BytesIO(data))
    name = f"{rcept_no}.xml" if f"{rcept_no}.xml" in zf.namelist() else zf.namelist()[0]
    return zf.read(name).decode("utf-8", "ignore")


# ---------------------------------------------------------------------------
# 인덱싱 / 검색
# ---------------------------------------------------------------------------

def _bigrams(text: str) -> set[str]:
    """공백 제거 후 2글자 단위 조각. 조사·띄어쓰기 차이에 강한 한국어 키워드 매칭용."""
    s = re.sub(r"[^\w]", "", text.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _keyword_score(query: str, doc: str) -> float:
    q = _bigrams(query)
    return len(q & _bigrams(doc)) / len(q) if q else 0.0


KEYWORD_WEIGHT = 0.5
CANDIDATES = 30


def hybrid_search(name: str, build, query: str, k: int = 5) -> list[tuple[float, str, dict]]:
    """공용 하이브리드 검색: 벡터 후보 30개 → 한국어 bigram 키워드 점수로 재정렬.

    name: Chroma 컬렉션 이름 (원문이 바뀌면 이름도 바뀌게 만든다 → 자동 재인덱싱)
    build: 인덱스가 비어 있을 때 호출, [(본문, 메타데이터)] 반환
    """
    col = _client().get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
    with _index_lock:
        if col.count() == 0:
            items = build()
            if not items:
                raise dart.DartError("검색할 본문을 찾지 못했습니다.")
            docs = [d for d, _ in items]
            vectors = _embeddings().embed_documents(docs)
            col.add(ids=[f"{name}-{i}" for i in range(len(docs))], documents=docs,
                    embeddings=vectors, metadatas=[m for _, m in items])
    res = col.query(query_embeddings=[_embeddings().embed_query(query)],
                    n_results=min(CANDIDATES, col.count()), include=["documents", "distances", "metadatas"])
    scored = sorted(
        (((1 - dist) + KEYWORD_WEIGHT * _keyword_score(query, doc)), doc, meta)
        for doc, dist, meta in zip(res["documents"][0], res["distances"][0], res["metadatas"][0])
    )
    return scored[::-1][:k]


def chunk_sections(sections: list[tuple[str, str]], meta: dict) -> list[tuple[str, dict]]:
    out = []
    for path, text in sections:
        for chunk in _chunk(text):
            out.append((f"[{path}]\n{chunk}", {**meta, "section": path}))
    return out
