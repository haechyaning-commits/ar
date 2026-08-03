"""
마스킹 + 자체검증 모듈.

- 실제 리댁션(텍스트 레이어 제거) + 항목별 부분/완전마스킹 텍스트 재삽입
- 저장 전 자체 재검증 (6.5.1-1): 재추출해서 승인된 값이 남아있으면 저장 중단
- 회전 텍스트 경고 (6.5.1-4), 숨겨진 콘텐츠 경고 (6.5.1-2), 메타데이터 제거 (6.5.1-3)
- 처리 원자성 (6.5.2): 임시 파일에 먼저 쓰고, 검증 통과 후에만 최종 파일로 교체

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from detector import Finding
from pdf_extract import SpanRef, extract_text_and_spans, spans_covering


# ---------------------------------------------------------------------------
# 항목별 마스킹 형태 (설계서 6.5)
# ---------------------------------------------------------------------------
def mask_name(word: str) -> str:
    if len(word) <= 2:
        return word[0] + "*"
    return word[0] + "*" * (len(word) - 2) + word[-1]


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11:
        return f"{digits[:3]}-{'*' * 4}-{digits[7:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-{'*' * 3}-{digits[6:]}"
    return "*" * len(value)


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    # 로컬파트가 1글자면 남길 게 없으므로 전부 마스킹 (버그: 이전엔 이 경우 마스킹이 0글자 -> 원본 그대로 노출됨)
    if len(local) <= 1:
        return "*" * len(local) + "@" + domain
    keep = min(2, len(local) - 1)
    return local[:keep] + "*" * (len(local) - keep) + "@" + domain


def mask_address(value: str) -> str:
    # 시/도 등 앞부분만 남기고 상세주소는 마스킹 (공백은 유지해 어절 구분은 보이게)
    parts = value.split(" ", 1)
    if len(parts) == 1:
        return "*" * len(value)
    prefix, rest = parts
    masked_rest = "".join(c if c == " " else "*" for c in rest)
    return f"{prefix} {masked_rest}"


def mask_full(value: str) -> str:
    # 완전마스킹: 길이·구분자(하이픈 등)는 유지, 영문/숫자만 전부 마스킹
    return "".join("*" if c.isalnum() else c for c in value)


def mask_value(f: Finding) -> str:
    if f.type == "이름":
        return mask_name(f.value)
    if f.type == "전화번호":
        return mask_phone(f.value)
    if f.type == "이메일":
        return mask_email(f.value)
    if f.type == "주소":
        return mask_address(f.value)
    if f.type in ("주민등록번호", "여권번호", "계좌번호"):
        return mask_full(f.value)
    return "*" * len(f.value)


# ---------------------------------------------------------------------------
# 리댁션 적용 (위치 기반, v15)
# ---------------------------------------------------------------------------
# 주의(실측으로 확인한 함정): add_redact_annot(text=..., fontsize=N)에 폰트 크기를
# 지정해도 PyMuPDF는 원본 글자의 좁은 사각형 안에 억지로 맞추면서 자동으로 글자를
# 줄여버린다 (예: 11pt를 줘도 실제로는 8~9pt로 축소되어 삽입됨). 그래서 리댁션은
# "지우기"만 담당하게 하고, 마스킹된 텍스트는 원본과 같은 폰트 크기·위치(baseline)를
# 직접 계산해서 별도로 그려 넣는 방식으로 처리한다.
#
# (v15) 이전엔 승인된 항목의 "문자열 값"으로 페이지 전체를 재검색해서 마스킹했음
# -> 같은 값이 문서 여러 곳에 있으면, 검토자가 특정 항목만 체크 해제해도 같은 값을
# 가진 다른 위치가 남아있으면 전부 같이 마스킹되는 버그가 있었음(masked_counts
# 집계도 실제 처리 건수와 안 맞았음). Finding.start/end를 pdf_extract의 스팬
# 매핑으로 되짚어, 승인된 "그 위치"만 정확히 마스킹하도록 바꿈.
def _plan_for_finding(f: Finding, full_text: str, spans: list[SpanRef]):
    """f.start:f.end 위치가 실제로 f.value와 일치하는지 확인하고,
    그 구간과 겹치는 스팬(들)에 대한 (page_index, rect, masked_segment, baseline, fontsize) 계획을 만든다.
    구간이 여러 스팬에 걸쳐 있어도(폰트가 중간에 바뀌는 등) 스팬별로 나눠서 처리한다.
    """
    if full_text[f.start:f.end] != f.value:
        # 탐지 시점 오프셋과 현재 문서가 어긋남(방어적 점검) -> 위치를 못 찾은 것으로
        # 처리해 self_check가 저장을 막게 함 ("확실하지 않으면 저장하지 않는다")
        return []

    masked = mask_value(f)
    plans = []
    for span in spans_covering(spans, f.start, f.end):
        local_lo = max(f.start, span.start) - span.start
        local_hi = min(f.end, span.end) - span.start
        seg_lo = max(f.start, span.start) - f.start
        seg_hi = min(f.end, span.end) - f.start

        prefix_width = fitz.get_text_length(span.text[:local_lo], fontname="korea", fontsize=span.fontsize)
        seg_width = fitz.get_text_length(span.text[local_lo:local_hi], fontname="korea", fontsize=span.fontsize)
        ox, oy = span.origin
        rect = fitz.Rect(ox + prefix_width, span.bbox[1], ox + prefix_width + seg_width, span.bbox[3])
        baseline = (ox + prefix_width, oy)
        plans.append((span.page_index, rect, masked[seg_lo:seg_hi], baseline, span.fontsize))
    return plans


def apply_masking(doc: fitz.Document, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """승인된 항목만, 각자의 위치에서만 마스킹한다.
    반환값: (mapped, unmapped) -- mapped는 위치를 찾아 리댁션을 실제로 시도한 항목
    (self_check가 진짜로 지워졌는지 재확인), unmapped는 위치 자체를 못 찾아 아예
    손대지 못한 항목(무조건 저장을 막아야 함 -- self_check로는 못 잡는 실패 유형이라
    별도로 반환한다).
    """
    full_text, spans = extract_text_and_spans(doc)
    approved = [f for f in findings if f.approved]

    plans_by_page: dict[int, list[tuple[fitz.Rect, str, tuple[float, float], float]]] = {}
    mapped: list[Finding] = []
    unmapped: list[Finding] = []
    for f in approved:
        plans = _plan_for_finding(f, full_text, spans)
        if not plans:
            unmapped.append(f)
            continue
        mapped.append(f)
        for page_index, rect, masked_segment, baseline, size in plans:
            plans_by_page.setdefault(page_index, []).append((rect, masked_segment, baseline, size))

    for page_index, plans in plans_by_page.items():
        page = doc[page_index]
        for rect, _masked_segment, _baseline, _size in plans:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()

        for _rect, masked_segment, baseline, size in plans:
            page.insert_text(baseline, masked_segment, fontname="korea", fontsize=size)

    return mapped, unmapped


# ---------------------------------------------------------------------------
# 자체 재검증 (6.5.1-1)
# ---------------------------------------------------------------------------
def self_check(doc: fitz.Document, mapped: list[Finding]) -> list[str]:
    """apply_masking이 위치를 찾아 리댁션을 시도한 항목(mapped)만 대상으로,
    그 위치에서 원본 값이 실제로 사라졌는지 재확인한다 (리댁션 API가 특정
    케이스에서 조용히 실패하는 경우를 잡아내기 위함, 6.5.1-1).

    위치 자체를 못 찾은 항목(unmapped)은 여기서 검사할 대상이 아예 없으므로
    -- 아무것도 안 바뀐 상태와 구분이 안 됨 -- 호출하는 쪽(mask_pdf)에서
    unmapped를 무조건 실패로 취급해야 한다.
    """
    # pdf_extract는 마스킹 전후로 항상 같은 방식(dict/sort=True)으로 추출하고,
    # 모든 마스킹 함수가 원본과 같은 길이의 문자열을 돌려주므로(mask_name/mask_phone/...),
    # 마스킹 후에도 같은 start:end 위치를 그대로 다시 비교할 수 있다.
    full_text, _ = extract_text_and_spans(doc)
    return [f.value for f in mapped if full_text[f.start:f.end] == f.value]


# ---------------------------------------------------------------------------
# 회전 텍스트 / 숨겨진 콘텐츠 경고, 메타데이터 제거
# ---------------------------------------------------------------------------
def has_rotated_text(doc: fitz.Document) -> bool:
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                _, dy = line.get("dir", (1, 0))
                if abs(dy) > 0.01:
                    return True
    return False


def has_hidden_content(doc: fitz.Document) -> bool:
    for page in doc:
        if list(page.annots() or []):
            return True
        if page.first_widget is not None:
            return True
    if doc.embfile_count() > 0:
        return True
    # 설계서 6.5.1-2가 약속한 OCG(선택적 콘텐츠 레이어) 검사가 빠져있던 부분 -> 추가
    return bool(doc.get_ocgs())


def scrub_metadata(doc: fitz.Document) -> None:
    doc.set_metadata({})
    try:
        doc.set_xml_metadata("")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 전체 오케스트레이션
# ---------------------------------------------------------------------------
@dataclass
class MaskResult:
    success: bool
    output_path: str | None
    masked_counts: dict[str, int] = field(default_factory=dict)
    leftover: list[str] = field(default_factory=list)
    rotated_text_warning: bool = False
    hidden_content_warning: bool = False


def mask_pdf(input_path: str, findings: list[Finding], output_path: str) -> MaskResult:
    doc = fitz.open(input_path)

    rotated_warning = has_rotated_text(doc)
    hidden_warning = has_hidden_content(doc)

    mapped, unmapped = apply_masking(doc, findings)
    # mapped: 리댁션을 시도한 항목 -> self_check로 실제로 지워졌는지 재확인
    # unmapped: 위치 자체를 못 찾아 손도 못 댄 항목 -> self_check로는 구분이
    # 안 되므로(그 자리에 다른 내용이 있으면 "지워짐"과 똑같이 보임) 무조건 실패 처리
    leftover = self_check(doc, mapped) + [f.value for f in unmapped]

    if leftover:
        # 6.5.1: 재검증 실패 -> 저장 자체를 하지 않음, 원본/입력 파일 무변경
        doc.close()
        return MaskResult(False, None, {}, leftover, rotated_warning, hidden_warning)

    scrub_metadata(doc)

    # 6.5.2: 임시 파일에 먼저 쓰고, 검증 통과 후에만 최종 파일로 원자적 교체
    tmp_path = output_path + ".tmp"
    doc.save(tmp_path)
    doc.close()
    os.replace(tmp_path, output_path)

    counts: dict[str, int] = {}
    for f in findings:
        if f.approved:
            counts[f.type] = counts.get(f.type, 0) + 1

    return MaskResult(True, output_path, counts, [], rotated_warning, hidden_warning)
