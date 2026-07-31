"""
마스킹 + 자체검증 모듈.

- 실제 리댁션(텍스트 레이어 제거) + 항목별 부분/완전마스킹 텍스트 재삽입 (회전된 텍스트도 진행 방향에 맞춰 처리)
- 저장 전 자체 재검증 (6.5.1-1): 재추출해서 승인된 값이 남아있으면 저장 중단
- 회전 텍스트 경고 (6.5.1-4)
- 숨겨진 콘텐츠(6.5.1-2): 주석/폼필드/첨부파일(텍스트 디코딩 가능한 것)은 독립적으로 스캔해
  PII를 실제로 마스킹 + 스크러빙 후 재검증. OCG(선택적 콘텐츠 레이어)는 존재 경고만 유지(미해결)
- 메타데이터 제거 (6.5.1-3)
- 처리 원자성 (6.5.2): 임시 파일에 먼저 쓰고, 검증 통과 후에만 최종 파일로 교체

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from detector import Finding, detect_all


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
def _dir_to_rotate(dx: float, dy: float) -> int:
    """텍스트 진행 방향 벡터(line["dir"])를 insert_text()의 rotate 값으로 변환.
    실측 확인: rotate=0->dir=(1,0), 90->(0,-1), 180->(-1,0), 270->(0,1)."""
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 180
    return 270 if dy > 0 else 90


def _plan_replacements(page: fitz.Page, value_to_masked: dict[str, str]):
    """리댁션 전에, 각 span에서 대상 문자열의 정확한 baseline 위치와 폰트 크기를 계산.

    ⚠ 회전된(세로쓰기 등) 텍스트도 진행 방향 벡터(dir)를 따라 폭을 투영해서 계산한다.
    이전 버전은 항상 가로 진행(dir=(1,0))을 가정했기 때문에, 회전된 스팬에서는
    엉뚱한 좌표의 계획이 나왔고 그 결과 아래 search_for() 대체 탐색과 좌표가 달라
    같은 값이 두 번(잘못된 위치 + 올바른 위치) 리댁션·재삽입되는 문제가 있었다
    (실측으로 확인, mvp/tests/test_masker.py 회귀 테스트로 고정해 둠).
    """
    plans = []  # (rect, masked_text, baseline_point, fontsize, rotate)
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1, 0))
            rotate = _dir_to_rotate(dx, dy)
            for span in line.get("spans", []):
                text = span["text"]
                size = span.get("size", 11)
                ox, oy = span["origin"]
                bx0, by0, bx1, by1 = span["bbox"]
                for value, masked in value_to_masked.items():
                    start = 0
                    while (idx := text.find(value, start)) != -1:
                        prefix_width = fitz.get_text_length(text[:idx], fontname="korea", fontsize=size)
                        value_width = fitz.get_text_length(value, fontname="korea", fontsize=size)
                        baseline = (ox + prefix_width * dx, oy + prefix_width * dy)
                        end_x = baseline[0] + value_width * dx
                        end_y = baseline[1] + value_width * dy
                        if rotate in (0, 180):
                            x0, x1 = sorted((baseline[0], end_x))
                            rect = fitz.Rect(x0, by0, x1, by1)
                        else:
                            y0, y1 = sorted((baseline[1], end_y))
                            rect = fitz.Rect(bx0, y0, bx1, y1)
                        plans.append((rect, masked, baseline, size, rotate))
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
                    plans.append((rect, masked, None, 11, 0))

        for rect, masked, baseline, size, rotate in plans:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()

        for rect, masked, baseline, size, rotate in plans:
            point = baseline if baseline is not None else (rect.x0, rect.y1 - 2)
            page.insert_text(point, masked, fontname="korea", fontsize=size, rotate=rotate)

    return value_to_masked


# ---------------------------------------------------------------------------
# 자체 재검증 (6.5.1-1)
# ---------------------------------------------------------------------------
def self_check(doc: fitz.Document, original_values) -> list[str]:
    # sort=True 필수 (v11 PoC 실측: 없으면 재추출 순서가 흐트러질 수 있음)
    full_text = "\n".join(page.get_text(sort=True) for page in doc)
    return [v for v in original_values if v in full_text]


def raw_byte_leftover(saved_path: str, original_values) -> list[str]:
    """⚠ 실측으로 발견한 맹점에 대한 안전망: self_check()는 get_text() 등 '현재
    참조되는 구조'만 보기 때문에, 리댁션이 다른 주석을 덮어 삭제하면서 그 주석의
    원본 어피어런스 스트림 객체가 참조는 끊겼지만 가비지 컬렉션 없이 저장되어
    파일 바이트에는 그대로 남는 경우를 못 잡는다 (FreeText 주석으로 재현·확인,
    mvp/tests/test_masker.py). doc.save(..., garbage=4)로 근본 원인은 없앴지만,
    이 함수는 '저장된 파일'을 직접 바이트 단위로 다시 열어 원본 값이 조금이라도
    남아있는지 확인하는 최종 방어선이다 -- 설계서가 PoC에서 주장한 "PDF 원본 바이트
    레벨에서 재검색해 완전히 사라짐을 확인"을 실제로 매 처리마다 수행하는 것.

    참고(실측 확인): 한글은 CID 임베디드 폰트로 저장되어 콘텐츠 스트림에 애초에
    리터럴 UTF-8 바이트로 존재하지 않는다 (글리프 인덱스로 저장됨) -- 그래서 이 검사는
    전화번호/주민번호/계좌번호/이메일처럼 라틴 폰트로 그려지는 값에는 유효하지만
    한글 이름/주소의 orphan 객체 잔존까지는 못 잡는다. 한글 값은 doc.save(garbage=4)로
    orphan 객체 자체를 없애는 근본 조치가 유일한 방어선이므로 그 옵션을 반드시 유지할 것."""
    with open(saved_path, "rb") as f:
        raw = f.read()
    return [v for v in original_values if v.encode("utf-8") in raw]


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


# ---------------------------------------------------------------------------
# 숨겨진 콘텐츠 스크러빙 (6.5.1-2)
# ---------------------------------------------------------------------------
# ⚠ 이전 버전은 has_hidden_content()로 "존재 여부"만 boolean으로 보고하고 실제 내용은
# 전혀 건드리지 않았다. 그 결과 검토자가 승인한 적도 없는 개인정보가 주석/첨부파일에
# 그대로 남은 채 "재검증 통과"로 표시되는 문제가 실측으로 확인됐다 (mvp/tests/test_masker.py).
# 아래는 본문과 별개로, 이 영역들을 독립적으로 detect_all()에 통과시켜 PII가 나오면
# 즉시 마스킹한다 -- 검토자가 미리 볼 화면이 없는 영역이라 "확실하지 않으면 가리는" 기본값을
# 그대로 적용(설계서 6번 정책 표: 성명 처리 원칙과 동일한 방향).
# OCG(선택적 콘텐츠 레이어)는 꺼진 레이어의 콘텐츠가 기본 텍스트 추출에서 아예 빠져
# (회전 텍스트와 비슷하게 탐지 자체가 안 되는) 별도의 구조적 난제라 이번 수정 범위에서는
# 제외하고 has_hidden_content()의 존재 경고만 유지한다.
def _mask_pii_in_text(text: str) -> tuple[str, list[str]]:
    """반환값: (마스킹된 텍스트, 마스킹된 원본 값 목록). 원본 값 목록은 저장 후
    바이트 레벨 재검증(_raw_byte_leftover)에서 '이 값들이 진짜 지워졌는지'를 다시 확인하는 데 쓴다."""
    findings = detect_all(text)
    if not findings:
        return text, []
    new_text = text
    for f in sorted(findings, key=lambda f: f.start, reverse=True):
        new_text = new_text[:f.start] + mask_value(f) + new_text[f.end:]
    return new_text, [f.value for f in findings]


def _scrub_annotations(page: fitz.Page) -> list[str]:
    masked: list[str] = []
    for annot in page.annots() or []:
        content = annot.info.get("content", "")
        if not content:
            continue
        new_content, found = _mask_pii_in_text(content)
        if found:
            annot.set_info(content=new_content)
            annot.update()
            masked += found
    return masked


def _scrub_widgets(page: fitz.Page) -> list[str]:
    masked: list[str] = []
    for widget in page.widgets() or []:
        value = widget.field_value
        if not value:
            continue
        new_value, found = _mask_pii_in_text(str(value))
        if found:
            widget.field_value = new_value
            widget.update()
            masked += found
    return masked


def _scrub_embedded_files(doc: fitz.Document) -> list[str]:
    masked: list[str] = []
    for name in list(doc.embfile_names()):
        try:
            text = doc.embfile_get(name).decode("utf-8")
        except (UnicodeDecodeError, RuntimeError, ValueError):
            # 텍스트로 디코딩 안 되는 첨부(바이너리 등)는 내용을 스캔할 방법이 없어
            # 건드리지 않는다 -- has_hidden_content() 경고로만 존재를 알림
            continue
        new_text, found = _mask_pii_in_text(text)
        if not found:
            continue
        info = doc.embfile_info(name)
        doc.embfile_del(name)
        doc.embfile_add(name, new_text.encode("utf-8"), filename=info.get("filename", name))
        masked += found
    return masked


def scrub_hidden_content(doc: fitz.Document) -> list[str]:
    """주석/폼필드/첨부파일(텍스트 디코딩 가능한 것)에서 PII를 찾아 즉시 마스킹.
    반환값: 마스킹된 원본 값 전체 목록(저장 후 바이트 레벨 재검증에 사용)."""
    masked: list[str] = []
    for page in doc:
        masked += _scrub_annotations(page)
        masked += _scrub_widgets(page)
    masked += _scrub_embedded_files(doc)
    return masked


def hidden_content_leftover(doc: fitz.Document) -> list[str]:
    """스크러빙 후 재검사 -- 텍스트로 디코딩 가능한 숨김 영역에 PII 패턴이 남아있으면 보고.
    본문 self_check()와 같은 역할을 숨김 콘텐츠에 대해서도 수행해, 스크러빙 실패를
    '재검증 통과'로 잘못 보고하지 않도록 한다."""
    leftover: list[str] = []
    for page in doc:
        for annot in page.annots() or []:
            leftover += [f.value for f in detect_all(annot.info.get("content", ""))]
        for widget in page.widgets() or []:
            if widget.field_value:
                leftover += [f.value for f in detect_all(str(widget.field_value))]
    for name in doc.embfile_names():
        try:
            text = doc.embfile_get(name).decode("utf-8")
        except (UnicodeDecodeError, RuntimeError, ValueError):
            continue
        leftover += [f.value for f in detect_all(text)]
    return leftover


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

    value_to_masked = apply_masking(doc, findings)
    hidden_masked_values = scrub_hidden_content(doc)
    all_original_values = list(value_to_masked.keys()) + hidden_masked_values

    leftover = self_check(doc, value_to_masked.keys())
    leftover += hidden_content_leftover(doc)

    if leftover:
        # 6.5.1: 재검증 실패 -> 저장 자체를 하지 않음, 원본/입력 파일 무변경
        doc.close()
        return MaskResult(False, None, {}, leftover, rotated_warning, hidden_warning)

    scrub_metadata(doc)

    # 6.5.2: 임시 파일에 먼저 쓰고, 검증 통과 후에만 최종 파일로 원자적 교체
    # garbage=4: 리댁션으로 삭제된 주석 등이 남긴 참조 끊긴(orphan) 객체를 실제로 제거.
    # 이게 없으면 참조는 끊겨도 원본 텍스트 바이트가 파일에 그대로 남을 수 있음(실측 확인).
    tmp_path = output_path + ".tmp"
    doc.save(tmp_path, garbage=4, deflate=True)
    doc.close()

    # 저장된 파일을 바이트 레벨로 다시 검증(self_check가 못 잡는 orphan 객체 케이스의 안전망)
    raw_leftover = raw_byte_leftover(tmp_path, all_original_values)
    if raw_leftover:
        os.remove(tmp_path)
        return MaskResult(False, None, {}, raw_leftover, rotated_warning, hidden_warning)

    os.replace(tmp_path, output_path)

    counts: dict[str, int] = {}
    for f in findings:
        if f.approved:
            counts[f.type] = counts.get(f.type, 0) + 1

    return MaskResult(True, output_path, counts, [], rotated_warning, hidden_warning)
