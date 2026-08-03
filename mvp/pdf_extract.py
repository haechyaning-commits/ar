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
                    ))
                emit("\n")  # 같은 줄 안의 탐지 규칙(라벨:값 등)이 다음 줄로 안 새도록 줄마다 구분
        emit("\n")  # 페이지 구분

    return "".join(parts), spans


def spans_covering(spans: list[SpanRef], start: int, end: int) -> list[SpanRef]:
    """[start, end) 구간과 겹치는 스팬들을 문서 내 등장 순서대로 반환."""
    return [s for s in spans if s.start < end and s.end > start]
