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
# 리댁션 적용
# ---------------------------------------------------------------------------
# 주의(실측으로 확인한 함정): add_redact_annot(text=..., fontsize=N)에 폰트 크기를
# 지정해도 PyMuPDF는 원본 글자의 좁은 사각형 안에 억지로 맞추면서 자동으로 글자를
# 줄여버림 (예: 11pt를 줘도 실제로는 8~9pt로 축소되어 삽입됨). 그래서 리댁션은
# "지우기"만 담당하게 하고, 마스킹된 텍스트는 원본과 같은 폰트 크기·위치(baseline)를
# 직접 계산해서 별도로 그려 넣는 방식으로 바꿈.
def _plan_replacements(page: fitz.Page, value_to_masked: dict[str, str]):
    """리댁션 전에, 각 span에서 대상 문자열의 정확한 baseline 위치와 폰트 크기를 계산."""
    plans = []  # (rect, masked_text, baseline_point, fontsize)
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"]
                size = span.get("size", 11)
                ox, oy = span["origin"]
                for value, masked in value_to_masked.items():
                    start = 0
                    while (idx := text.find(value, start)) != -1:
                        prefix_width = fitz.get_text_length(text[:idx], fontname="korea", fontsize=size)
                        value_width = fitz.get_text_length(value, fontname="korea", fontsize=size)
                        baseline = (ox + prefix_width, oy)
                        rect = fitz.Rect(ox + prefix_width, span["bbox"][1], ox + prefix_width + value_width, span["bbox"][3])
                        plans.append((rect, masked, baseline, size))
                        start = idx + len(value)
    return plans


def apply_masking(doc: fitz.Document, findings: list[Finding]) -> dict[str, str]:
    approved = [f for f in findings if f.approved]
    value_to_masked: dict[str, str] = {}
    for f in approved:
        value_to_masked.setdefault(f.value, mask_value(f))

    for page in doc:
        plans = _plan_replacements(page, value_to_masked)
        # 계획한 위치가 있으면 그대로, 없으면(다중 span에 걸친 값 등) search_for로 대체 탐색
        planned_rects = {tuple(round(c, 1) for c in p[0]) for p in plans}
        for value, masked in value_to_masked.items():
            for rect in page.search_for(value):
                key = tuple(round(c, 1) for c in rect)
                if key not in planned_rects:
                    plans.append((rect, masked, None, 11))

        for rect, masked, baseline, size in plans:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()

        for rect, masked, baseline, size in plans:
            point = baseline if baseline is not None else (rect.x0, rect.y1 - 2)
            page.insert_text(point, masked, fontname="korea", fontsize=size)

    return value_to_masked


# ---------------------------------------------------------------------------
# 자체 재검증 (6.5.1-1)
# ---------------------------------------------------------------------------
def self_check(doc: fitz.Document, original_values) -> list[str]:
    # sort=True 필수 (v11 PoC 실측: 없으면 재추출 순서가 흐트러질 수 있음)
    full_text = "\n".join(page.get_text(sort=True) for page in doc)
    return [v for v in original_values if v in full_text]


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
    masked_counts: dict[str, dict[str, int]] = field(default_factory=dict)  # type -> {자동|수동: 건수} (6.7)
    leftover: list[str] = field(default_factory=list)
    rotated_text_warning: bool = False
    hidden_content_warning: bool = False


def mask_pdf(input_path: str, findings: list[Finding], output_path: str) -> MaskResult:
    doc = fitz.open(input_path)

    rotated_warning = has_rotated_text(doc)
    hidden_warning = has_hidden_content(doc)

    value_to_masked = apply_masking(doc, findings)
    leftover = self_check(doc, value_to_masked.keys())

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

    counts: dict[str, dict[str, int]] = {}
    for f in findings:
        if f.approved:
            by_source = counts.setdefault(f.type, {})
            by_source[f.source] = by_source.get(f.source, 0) + 1

    return MaskResult(True, output_path, counts, [], rotated_warning, hidden_warning)
