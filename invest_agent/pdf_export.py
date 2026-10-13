"""마크다운 보고서 → 제출용 PDF (A4, 표지 + 머리말·꼬리말 + 쪽번호).

markdown(표 확장) → HTML → PyMuPDF Story 로 배치한다.
PyMuPDF에 한글 대체 글꼴이 내장돼 있어 별도 폰트 설치 없이 macOS·Linux·Windows에서 동작한다.
"""

import re
from datetime import date
from pathlib import Path

import markdown
import pymupdf

PAGE = pymupdf.paper_rect("a4")
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 52, 64, 58
BODY = pymupdf.Rect(MARGIN_X, MARGIN_TOP, PAGE.width - MARGIN_X, PAGE.height - MARGIN_BOTTOM)
NAVY, ACCENT = "#14284b", "#2f6fdf"

CSS = f"""
body {{ font-family: sans-serif; font-size: 9.2pt; line-height: 1.55; color: #1f2430; }}
h2 {{ font-size: 12.5pt; color: #ffffff; background-color: {NAVY}; padding: 4pt 8pt; margin: 16pt 0 8pt 0; }}
h3 {{ font-size: 10.5pt; color: {NAVY}; border-bottom: 0.8px solid #c9d3e3; padding-bottom: 2pt; margin: 11pt 0 5pt 0; }}
p {{ margin: 0 0 7pt 0; }}
sup {{ font-size: 6.2pt; color: {ACCENT}; }}
.summary {{ background-color: #f2f5fa; padding: 8pt 10pt; margin: 0 0 6pt 0; }}
.summary p {{ margin: 0; font-size: 9.6pt; line-height: 1.65; }}
li {{ margin: 0 0 3pt 0; }}
ul {{ margin: 0 0 6pt 0; }}
strong {{ color: {NAVY}; }}
blockquote {{ margin: 6pt 0 8pt 0; padding: 5pt 9pt; background-color: #f2f5fa; border-left: 3px solid {ACCENT};
              font-size: 8.4pt; color: #3d4657; }}
table {{ border-collapse: collapse; margin: 4pt 0 8pt 0; font-size: 8pt; }}
th {{ color: {NAVY}; font-weight: bold; border: 0.6px solid #b8c4d6; border-bottom: 1.4px solid {NAVY}; padding: 3pt 4pt; }}
td {{ border: 0.6px solid #cfd7e3; padding: 2.5pt 4pt; }}
code {{ font-family: monospace; font-size: 8pt; }}
.ref {{ font-size: 7.6pt; line-height: 1.4; color: #3d4657; }}
.disclaimer {{ font-size: 7.6pt; color: #6b7385; border-top: 0.6px solid #c9d3e3; padding-top: 4pt; margin-top: 10pt; }}
"""

COVER_CSS = f"""
body {{ font-family: sans-serif; color: #1f2430; }}
.label {{ font-size: 9pt; color: {ACCENT}; font-weight: bold; letter-spacing: 1pt; }}
.title {{ font-size: 26pt; font-weight: bold; color: {NAVY}; line-height: 1.25; margin: 8pt 0 6pt 0; }}
.subtitle {{ font-size: 11pt; color: #4a5468; margin-bottom: 24pt; }}
.row {{ font-size: 9.5pt; padding: 6pt 2pt; border-bottom: 0.6px solid #d5dce8; }}
.k {{ color: {ACCENT}; font-weight: bold; }}
.warn {{ margin-top: 18pt; padding: 8pt 10pt; background-color: #fff4e5; border-left: 3px solid #e08a00; font-size: 8.6pt; }}
.note {{ margin-top: 26pt; font-size: 8pt; color: #6b7385; line-height: 1.5; }}
"""


def _split_title(md_text: str) -> tuple[str, str]:
    m = re.search(r"^#\s+(.+)$", md_text, re.M)
    if not m:
        return "투자 가능성 평가 보고서", md_text
    return m.group(1).strip(), md_text[:m.start()] + md_text[m.end():]


def _soft_wrap_urls(html: str) -> str:
    """긴 URL이 줄을 넘지 않도록 '/'·'?'·'=' 뒤에 줄바꿈 가능 지점을 넣는다."""
    def soft(m: re.Match) -> str:
        scheme, rest = m.group(1).split("://", 1)
        return scheme + "://" + re.sub(r"([/?=])", r"\1<wbr>", rest)
    return re.sub(r"(https?://[^\s<]+)", soft, html)


_HANGUL = re.compile(r"[가-힣]")
_CITE_RUN = re.compile(r"(?:[ \t]*\[E\d+\])+")


def _footnotes(md_body: str) -> str:
    """본문 [E#] 인용을 REFERENCE 순서의 각주 번호(위 첨자)로, REFERENCE 항목은 [번호]로 바꾼다."""
    head, sep, ref = md_body.partition("## REFERENCE")
    num: dict[str, int] = {}
    for i, group in enumerate(re.findall(r"^- ((?:\[E\d+\])+)", ref, re.M), 1):
        for eid in re.findall(r"E\d+", group):
            num[eid] = i

    def to_sup(m: re.Match) -> str:
        ids = re.findall(r"E\d+", m.group(0))
        ns = sorted({num[i] for i in ids if i in num})
        return f"<sup>{','.join(map(str, ns))}</sup>" if ns else ""

    head = _CITE_RUN.sub(to_sup, head)
    ref = re.sub(r"^- ((?:\[E\d+\])+)",
                 lambda m: f"- [{num[re.findall(r'E[0-9]+', m.group(1))[0]]}]", ref, flags=re.M)
    return head + sep + ref


def _keep_all(html: str) -> str:
    """한글 어절 중간에서 줄이 바뀌지 않게 어절 단위로 묶는다 (CSS word-break: keep-all 대용)."""
    def wrap_text(m: re.Match) -> str:
        parts = re.split(r"( +)", m.group(1))
        return ">" + "".join(f'<span style="white-space:nowrap">{t}</span>'
                             if _HANGUL.search(t) and "http" not in t else t for t in parts) + "<"
    return re.sub(r">([^<>]+)<", wrap_text, html)


def _summary_box(html: str) -> str:
    m = re.search(r"(<h2>SUMMARY</h2>)(.*?)(?=<h2>)", html, re.S)
    if not m:
        return html
    return html[:m.start()] + m.group(1) + f'<div class="summary">{m.group(2)}</div>' + html[m.end():]


def _body_html(md_body: str) -> tuple[str, str | None]:
    warning = None
    m = re.match(r"\s*>\s*⚠️\s*품질 경고:\s*(.+)", md_body)
    if m:
        warning = m.group(1).strip()
        md_body = md_body[m.end():]
    md_body = re.sub(r"^\s*(?:-{3,}|\*{3,})\s*$", "", _footnotes(md_body), flags=re.M)
    html = markdown.markdown(md_body, extensions=["tables", "sane_lists"])
    html = _summary_box(html)
    # REFERENCE 이후는 작은 글씨로, 마지막 면책 문단은 별도 스타일로
    parts = re.split(r"(<h2>REFERENCE</h2>)", html, maxsplit=1)
    if len(parts) == 3:
        tail = parts[2]
        disc = re.search(r"<p>([^<]*투자 권유가 아닙니다[^<]*)</p>\s*$", tail)
        if disc:
            tail = tail[:disc.start()] + f'<p class="disclaimer">{disc.group(1)}</p>'
        html = parts[0] + parts[1] + f'<div class="ref">{tail}</div>'
    return _keep_all(_soft_wrap_urls(html)), warning


def _cover_html(title: str, warning: str | None) -> str:
    company = re.sub(r"\s*투자 가능성 평가 보고서\s*$", "", title) or title
    rows = [
        ("평가 대상", company),
        ("평가 일자", date.today().strftime("%Y년 %m월 %d일")),
        ("보고서 유형", "Physical AI·로보틱스 스타트업 투자 가능성 평가"),
        ("작성 방식", "LangGraph Supervisor 멀티 에이전트 (근거 충분성 판정 → 작성 → 품질 평가 루프)"),
        ("데이터 출처", "OpenDART 기업개황·공시·감사보고서, 산업 리포트, 기업 공개 자료"),
        ("평가 체계", "창업자 5 · 시장성 30 · 제품·기술력 25 · 경쟁 우위 20 · 실적 10 · 투자조건 10"),
    ]
    table = "".join(f'<div class="row"><span class="k">{k}</span>&#160;&#160;&#160;{v}</div>' for k, v in rows)
    warn = f'<div class="warn"><b>품질 경고</b> — {warning}</div>' if warning else ""
    return (f'<div class="label">INVESTMENT REVIEW REPORT</div>'
            f'<div class="title">{company}<br/>투자 가능성 평가 보고서</div>'
            f'<div class="subtitle">SUMMARY · COMPANY &amp; TEAM · TECHNOLOGY · MARKET · INVESTMENT ASSESSMENT &amp; RISKS</div>'
            f"{table}{warn}"
            '<div class="note">본 보고서는 공개 자료만으로 작성된 평가 참고 자료이며 투자 권유가 아닙니다. '
            "본문의 위 첨자 번호는 REFERENCE의 출처 번호와 연결됩니다.</div>")


def _place(writer, story, first_rect=None):
    more = True
    first = True
    while more:
        device = writer.begin_page(PAGE)
        more, _ = story.place(first_rect if first and first_rect is not None else BODY)
        story.draw(device)
        writer.end_page()
        first = False


def _decorate(doc, title: str) -> None:
    """표지 띠, 본문 머리말(보고서 제목)·꼬리말(쪽번호) — 표지는 쪽번호 없음."""
    navy = pymupdf.pdfcolor["navy"] if "navy" in pymupdf.pdfcolor else (0.08, 0.16, 0.29)
    cover = doc[0]
    cover.draw_rect(pymupdf.Rect(0, 0, PAGE.width, 14), color=None, fill=(0.08, 0.16, 0.29))
    cover.draw_rect(pymupdf.Rect(0, PAGE.height - 8, PAGE.width, PAGE.height), color=None, fill=(0.18, 0.44, 0.87))
    total = doc.page_count - 1
    for i in range(1, doc.page_count):
        page = doc[i]
        # MuPDF Story는 앞 요소의 배경(제목 띠·요약 박스)을 다음 쪽 맨 위에 잘린 조각으로 다시 그린다.
        # 본문 시작선 주변 얇은 띠에 '완전히 들어가는' 도형만 지운다 (글자와 실제 내용은 유지).
        page.add_redact_annot(pymupdf.Rect(BODY.x0 - 12, BODY.y0 - 9, BODY.x1 + 12, BODY.y0 + 9))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                              text=pymupdf.PDF_REDACT_TEXT_NONE)
        y = MARGIN_TOP - 20
        page.insert_htmlbox(pymupdf.Rect(MARGIN_X, y - 12, PAGE.width - MARGIN_X - 90, y + 2),
                            f'<span style="font-family:sans-serif;font-size:7.5pt;color:#6b7385">{title}</span>')
        page.insert_htmlbox(pymupdf.Rect(PAGE.width - MARGIN_X - 90, y - 12, PAGE.width - MARGIN_X, y + 2),
                            '<div style="font-family:sans-serif;font-size:7.5pt;color:#6b7385;text-align:right">INVESTMENT REVIEW</div>')
        page.draw_line(pymupdf.Point(MARGIN_X, y + 4), pymupdf.Point(PAGE.width - MARGIN_X, y + 4),
                       color=navy, width=0.6)
        fy = PAGE.height - MARGIN_BOTTOM + 22
        page.draw_line(pymupdf.Point(MARGIN_X, fy - 8), pymupdf.Point(PAGE.width - MARGIN_X, fy - 8),
                       color=(0.8, 0.84, 0.9), width=0.4)
        page.insert_htmlbox(pymupdf.Rect(MARGIN_X, fy - 4, PAGE.width - MARGIN_X, fy + 10),
                            f'<div style="font-family:sans-serif;font-size:7.5pt;color:#6b7385;text-align:right">{i} / {total}</div>')
        page.insert_htmlbox(pymupdf.Rect(MARGIN_X, fy - 4, PAGE.width - MARGIN_X - 60, fy + 10),
                            '<span style="font-family:sans-serif;font-size:7pt;color:#9aa1b0">공개 자료 기반 평가 참고 자료 · 투자 권유 아님</span>')


_KEEP_WITH_NEXT = 100  # pt: 제목이 쪽 하단 이만큼 안에 걸리면 다음 쪽으로 넘긴다 (제목만 남는 쪽 방지)


def _number_headings(html: str) -> str:
    n = iter(range(1, 10_000))
    return re.sub(r"<(h[23])>", lambda m: f'<{m.group(1)} id="hd{next(n)}">', html)


def _orphan_headings(html: str) -> set[str]:
    """시험 배치로 쪽 하단에 걸리는 제목을 찾는다. MuPDF Story는 page-break-inside를 지원하지 않아 2단계로 처리."""
    story = pymupdf.Story(html=html, user_css=CSS)
    hits: set[str] = set()
    limit = BODY.y1 - _KEEP_WITH_NEXT

    def check(pos):
        if pos.heading and pos.id and pos.open_close & 1 and pos.rect[1] > limit:
            hits.add(pos.id)

    import io

    writer = pymupdf.DocumentWriter(io.BytesIO())
    more = True
    while more:
        device = writer.begin_page(PAGE)
        more, _ = story.place(BODY)
        story.element_positions(check)
        story.draw(device)
        writer.end_page()
    writer.close()
    return hits


def _break_before(html: str, ids: set[str]) -> str:
    """제목 앞에 빈 쪽 나눔 블록을 넣는다 (제목 자체에 걸면 테두리 조각이 앞 쪽에 남는다)."""
    for hid in ids:
        if f'always"></div><h2 id="{hid}">' in html or f'always"></div><h3 id="{hid}">' in html:
            continue
        html = re.sub(rf'(<h[23] id="{hid}">)', r'<div style="page-break-before:always"></div>\1', html, count=1)
    return html


def markdown_to_pdf(md_text: str, pdf_path: Path) -> Path:
    title, body = _split_title(md_text)
    body_html, warning = _body_html(body)
    body_html = _number_headings(body_html)
    for _ in range(4):  # 앞 제목을 넘기면 뒤 배치가 바뀌므로 더 이상 걸리는 제목이 없을 때까지 반복
        orphans = _orphan_headings(body_html)
        if not orphans:
            break
        body_html = _break_before(body_html, orphans)
    writer = pymupdf.DocumentWriter(str(pdf_path))
    _place(writer, pymupdf.Story(html=_cover_html(title, warning), user_css=COVER_CSS),
           first_rect=pymupdf.Rect(MARGIN_X + 6, 150, PAGE.width - MARGIN_X - 6, PAGE.height - 80))
    _place(writer, pymupdf.Story(html=body_html, user_css=CSS))
    writer.close()

    doc = pymupdf.open(str(pdf_path))
    _decorate(doc, title)
    doc.subset_fonts()
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    pdf_path.write_bytes(data)
    return pdf_path
