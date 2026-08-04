"""
PDF에서 평문(full_text)과, 그 평문의 각 구간이 실제로 어느 페이지/스팬(좌표)에서
왔는지 매핑(spans)을 함께 만드는 단일 진입점.

탐지(detector.detect_all)는 이 모듈이 만든 full_text의 오프셋을 기준으로 동작하고,
마스킹(masker.apply_masking)은 같은 방식으로 다시 추출한 spans를 이용해 그 오프셋을
실제 PDF 좌표로 되돌려 찾는다. 두 쪽이 항상 같은 추출 방식(get_text("dict", sort=True))을
쓰기 때문에, "탐지된 이름 3번째 글자가 정확히 이 페이지 이 스팬의 이 좌표다"라는
매핑이 어긋나지 않는다.

(v15 신규) 기존에는 탐지 시점엔 get_text("text", sort=True)로 평문만 뽑고, 마스킹
시점엔 findings의 위치정보(start/end)를 아예 쓰지 않고 문자열 값으로 페이지 전체를
재검색해서 마스킹했음 -> 같은 문자열이 여러 곳에 있으면 검토자가 체크 해제한 항목까지
같이 마스킹되는 버그가 있었음 (masker.py 이전 버전). 이 모듈을 도입해 위치 기반으로
바꿈.
"""
from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF


@dataclass
class SpanRef:
    page_index: int
    text: str
    origin: tuple[float, float]
    bbox: tuple[float, float, float, float]
    fontsize: float
    start: int   # full_text 안에서 이 스팬 텍스트가 시작하는 전역 offset
    end: int     # start + len(text)
    direction: tuple[float, float] = (1.0, 0.0)  # 텍스트 진행 방향 벡터 (line["dir"]) -- 회전 텍스트 처리용


@dataclass
class RectPlan:
    page_index: int
    rect: tuple[float, float, float, float]  # (x0, y0, x1, y1) -- 이 구간의 시각적 영역
    baseline: tuple[float, float]            # 텍스트를 다시 그릴 때 쓸 기준점
    fontsize: float
    seg_lo: int  # 요청한 (start, end) 안에서 이 조각이 시작하는 상대 위치
    seg_hi: int  # 요청한 (start, end) 안에서 이 조각이 끝나는 상대 위치
    rotate: int = 0  # insert_text()에 그대로 넘길 회전값 (0/90/180/270)


def _dir_to_rotate(dx: float, dy: float) -> int:
    """텍스트 진행 방향 벡터(line["dir"])를 insert_text()의 rotate 값으로 변환.
    실측 확인: rotate=0->dir=(1,0), 90->(0,-1), 180->(-1,0), 270->(0,1)."""
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 180
    return 270 if dy > 0 else 90


def page_sizes(doc: fitz.Document) -> list[tuple[float, float]]:
    """각 페이지의 (width, height). 6.2.2 템플릿 지문 인식이 좌표를 페이지
    크기 기준 상대값으로 정규화하는 데 씀 -- doc이 닫히기 전에 호출해야 함."""
    return [(page.rect.width, page.rect.height) for page in doc]


def extract_text_and_spans(doc: fitz.Document) -> tuple[str, list[SpanRef]]:
    parts: list[str] = []
    spans: list[SpanRef] = []
    offset = 0

    def emit(s: str) -> None:
        nonlocal offset
        if s:
            parts.append(s)
            offset += len(s)

    for page_index, page in enumerate(doc):
        page_dict = page.get_text("dict", sort=True)
        for block in page_dict.get("blocks", []):
            for line in block.get("lines", []):
                direction = tuple(line.get("dir", (1.0, 0.0)))
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text:
                        continue
                    start = offset
                    emit(text)
                    spans.append(SpanRef(
                        page_index=page_index,
                        text=text,
                        origin=tuple(span["origin"]),
                        bbox=tuple(span["bbox"]),
                        fontsize=span.get("size", 11),
                        start=start,
                        end=offset,
                        direction=direction,
                    ))
                emit("\n")  # 같은 줄 안의 탐지 규칙(라벨:값 등)이 다음 줄로 안 새도록 줄마다 구분
        emit("\n")  # 페이지 구분

    return "".join(parts), spans


def spans_covering(spans: list[SpanRef], start: int, end: int) -> list[SpanRef]:
    """[start, end) 구간과 겹치는 스팬들을 문서 내 등장 순서대로 반환."""
    return [s for s in spans if s.start < end and s.end > start]


def page_of(spans: list[SpanRef], pos: int) -> int:
    """pos(전역 offset)가 속한 페이지 번호(0-based). 6.3.4 페이지 단위 진행
    상태 표시에서, 탐지 항목이 어느 페이지 것인지 알아내는 데 씀."""
    for s in spans:
        if s.start <= pos < s.end:
            return s.page_index
    return 0


def rects_for_range(full_text: str, spans: list[SpanRef], start: int, end: int) -> list[RectPlan]:
    """[start, end) 구간과 겹치는 스팬(들)에 대한 실제 좌표 계획을 만든다.
    masker.py(실제 리댁션 위치 계산)와 6.3.1 수동 추가 화면(기존 탐지 항목을
    페이지 이미지 위에 오버레이로 보여줄 때)이 공유하는 좌표 계산 로직 -- 두 곳이
    각자 따로 계산하면 나중에 어긋날 수 있어 한 곳으로 모음.

    ⚠ 회전된(세로쓰기 등) 텍스트도 진행 방향 벡터(span.direction)를 따라 폭을
    투영해서 계산한다. 항상 가로 진행(dir=(1,0))을 가정하면 회전된 스팬에서는
    baseline/사각형이 엉뚱한 좌표로 계산된다 (실측으로 확인, mvp/tests/test_masker.py
    회귀 테스트로 고정해 둠).

    호출하는 쪽에서 미리 full_text[start:end]가 기대하는 값과 같은지 확인하는 걸
    전제로 함 (이 함수 자체는 그 검증을 하지 않음).
    """
    out = []
    for span in spans_covering(spans, start, end):
        local_lo = max(start, span.start) - span.start
        local_hi = min(end, span.end) - span.start
        seg_lo = max(start, span.start) - start
        seg_hi = min(end, span.end) - start

        dx, dy = span.direction
        rotate = _dir_to_rotate(dx, dy)
        prefix_width = fitz.get_text_length(span.text[:local_lo], fontname="korea", fontsize=span.fontsize)
        seg_width = fitz.get_text_length(span.text[local_lo:local_hi], fontname="korea", fontsize=span.fontsize)
        ox, oy = span.origin
        baseline = (ox + prefix_width * dx, oy + prefix_width * dy)
        end_x = baseline[0] + seg_width * dx
        end_y = baseline[1] + seg_width * dy
        bx0, by0, bx1, by1 = span.bbox
        if rotate in (0, 180):
            x0, x1 = sorted((baseline[0], end_x))
            rect = (x0, by0, x1, by1)
        else:
            y0, y1 = sorted((baseline[1], end_y))
            rect = (bx0, y0, bx1, y1)
        out.append(RectPlan(span.page_index, rect, baseline, span.fontsize, seg_lo, seg_hi, rotate))
    return out


# ---------------------------------------------------------------------------
# 6.3.1 수동 추가 (클릭/드래그) -- 화면 좌표를 다시 full_text의 (start, end)로
# ---------------------------------------------------------------------------
def _resolve_word_offset(spans: list[SpanRef], page_index: int, word: str, x0: float, y0: float) -> tuple[int, int] | None:
    """이 페이지에서 word와 텍스트가 같고 y가 겹치는 스팬들 중, x0에 가장 가까운
    위치를 골라 (start, end)를 돌려준다. 같은 단어가 한 줄에 여러 번 나오는
    경우(드문 case)에 좌표로 구분하기 위함."""
    best = None
    best_dist = None
    for span in spans:
        if span.page_index != page_index or word not in span.text:
            continue
        if not (span.bbox[1] - 1 <= y0 <= span.bbox[3] + 1):
            continue
        idx = 0
        while (local := span.text.find(word, idx)) != -1:
            prefix_width = fitz.get_text_length(span.text[:local], fontname="korea", fontsize=span.fontsize)
            candidate_x = span.origin[0] + prefix_width
            dist = abs(candidate_x - x0)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = (span.start + local, span.start + local + len(word))
            idx = local + len(word)
    return best


def resolve_word_at(
    full_text: str, page: fitz.Page, spans: list[SpanRef], page_index: int, point: tuple[float, float],
) -> tuple[int, int, str] | None:
    """point(PDF 좌표, 페이지 page_index 위)와 겹치는 단어를 찾아 (start, end, value)를
    돌려준다. 6.3.1 클릭 입력에 씀. 겹치는 단어가 없으면(빈 공간 클릭) None."""
    px, py = point
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        if x0 <= px <= x1 and y0 <= py <= y1:
            resolved = _resolve_word_offset(spans, page_index, word, x0, y0)
            if resolved is None:
                return None
            start, end = resolved
            return (start, end, full_text[start:end])
    return None


def resolve_region(
    full_text: str, page: fitz.Page, spans: list[SpanRef], page_index: int,
    rect: tuple[float, float, float, float],
) -> tuple[int, int, str] | None:
    """rect(PDF 좌표, 페이지 page_index 위)와 절반 이상 겹치는 단어들을 모아
    첫 단어 시작 ~ 마지막 단어 끝을 (start, end, value)로 돌려준다. 6.3.1 드래그
    입력에 씀. 영역 안에 인식 가능한 텍스트가 하나도 없으면(이미지/도장 등) None
    -- 이 도구는 텍스트 기반 마스킹만 지원하므로, 텍스트 없는 영역의 순수 그래픽
    리댁션은 아직 지원하지 않음(향후 확장 후보)."""
    rx0, ry0, rx1, ry1 = rect
    resolved: list[tuple[int, int]] = []
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        ix0, iy0 = max(x0, rx0), max(y0, ry0)
        ix1, iy1 = min(x1, rx1), min(y1, ry1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        overlap = (ix1 - ix0) * (iy1 - iy0)
        area = max((x1 - x0) * (y1 - y0), 1e-6)
        if overlap / area < 0.5:
            continue
        r = _resolve_word_offset(spans, page_index, word, x0, y0)
        if r is not None:
            resolved.append(r)
    if not resolved:
        return None
    start = min(s for s, _ in resolved)
    end = max(e for _, e in resolved)
    return (start, end, full_text[start:end])
