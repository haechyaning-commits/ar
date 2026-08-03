"""
마스킹 + 자체검증 모듈.

- 실제 리댁션(텍스트 레이어 제거, 위치 기반) + 항목별 부분/완전마스킹 텍스트 재삽입 (회전된 텍스트도 진행 방향에 맞춰 처리)
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
from pdf_extract import SpanRef, extract_text_and_spans, rects_for_range


# ---------------------------------------------------------------------------
# 항목별 마스킹 형태 (설계서 6.5)
# ---------------------------------------------------------------------------
def mask_name(word: str) -> str:
    if len(word) <= 2:
        return word[0] + "*"
    return word[0] + "*" * (len(word) - 2) + word[-1]


# PHONE_RE(detector.py)의 하이픈이 전부 선택(-?)이라, 실제 값은
# "010-1234-5678"/"01012345678"/"010-12345678" 등 길이가 제각각일 수 있음.
# 이전엔 항상 "XXX-****-XXXX" 고정 형태로 새로 만들어서, 하이픈 없는 번호가
# 들어오면 원본보다 2글자 긴 문자열이 나와 뒤 2자리가 잘려나가는 버그가 있었음
# (마스킹 함수는 길이를 보존해야 한다는 게 self_check의 전제 조건, 6.5.1-1 참고).
# 원본 문자열의 실제 구조(하이픈 위치·자릿수)를 그대로 유지한 채 가운데 자리만
# 마스킹해서, 형식에 관계없이 항상 원본과 같은 길이가 나오게 함.
_PHONE_STRUCTURE_RE = re.compile(r"^(01[016789])(-?)(\d{3,4})(-?)(\d{4})$")


def mask_phone(value: str) -> str:
    m = _PHONE_STRUCTURE_RE.match(value)
    if not m:
        return "*" * len(value)
    prefix, sep1, middle, sep2, last = m.groups()
    return f"{prefix}{sep1}{'*' * len(middle)}{sep2}{last}"


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


# 9번 표(확인 필요한 가정): 계좌/카드번호는 완전마스킹이 잠정 스펙이지만,
# 조회 가용성을 위해 끝자리를 남기는 부분마스킹으로 바뀔 수 있다고 명시돼 있음
# -> 하드코딩하지 말고 여기 상수 하나만 바꾸면 정책을 전환할 수 있게 함.
ACCOUNT_CARD_MASK_FULL = True  # True=완전마스킹(현재 잠정 스펙) / False=끝 4자리 유지


def _mask_partial_keep_last(value: str, keep: int = 4) -> str:
    """뒤에서 keep개의 영문/숫자만 남기고 나머지 영문/숫자는 마스킹, 구분자는 그대로 유지."""
    alnum_positions = [i for i, c in enumerate(value) if c.isalnum()]
    keep_positions = set(alnum_positions[-keep:])
    return "".join(
        c if (not c.isalnum()) or i in keep_positions else "*"
        for i, c in enumerate(value)
    )


def mask_account_or_card(value: str) -> str:
    if ACCOUNT_CARD_MASK_FULL:
        return mask_full(value)
    return _mask_partial_keep_last(value, keep=4)


def mask_value(f: Finding) -> str:
    if f.type == "이름":
        return mask_name(f.value)
    if f.type == "전화번호":
        return mask_phone(f.value)
    if f.type == "이메일":
        return mask_email(f.value)
    if f.type == "주소":
        return mask_address(f.value)
    if f.type in ("계좌번호", "카드번호"):
        return mask_account_or_card(f.value)
    if f.type in ("주민등록번호", "여권번호"):
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
#
# ⚠ 회전된(세로쓰기 등) 텍스트도 진행 방향 벡터(line["dir"])를 따라 폭을 투영해서
# 계산해야 한다 -- 항상 가로 진행을 가정하면 회전된 스팬에서 baseline/사각형이
# 엉뚱한 좌표로 계산된다 (실측으로 확인, mvp/tests/test_masker.py 회귀 테스트로
# 고정해 둠). 이 투영 계산 자체는 pdf_extract.rects_for_range가 담당한다
# (6.3.1 수동 추가 화면의 기존 항목 오버레이 표시와 공유하는 로직).
def _plan_for_finding(f: Finding, full_text: str, spans: list[SpanRef]):
    """f.start:f.end 위치가 실제로 f.value와 일치하는지 확인하고,
    그 구간과 겹치는 스팬(들)에 대한 (page_index, rect, masked_segment, baseline, fontsize, rotate) 계획을 만든다.
    구간이 여러 스팬에 걸쳐 있어도(폰트가 중간에 바뀌는 등) 스팬별로 나눠서 처리한다.
    """
    if full_text[f.start:f.end] != f.value:
        # 탐지 시점 오프셋과 현재 문서가 어긋남(방어적 점검) -> 위치를 못 찾은 것으로
        # 처리해 self_check가 저장을 막게 함 ("확실하지 않으면 저장하지 않는다")
        return []

    masked = mask_value(f)
    if len(masked) != len(f.value):
        # 아래 좌표/슬라이싱 계산 전부가 "마스킹 함수는 항상 원본과 같은 길이를
        # 돌려준다"는 전제에 기대고 있음 (self_check도 마찬가지, 6.5.1-1 참고).
        # 이 전제가 깨지면(예: mask_phone이 "010-****-5678" 고정 형태로 재구성해서
        # 하이픈 없는 원본보다 길어졌던 버그) 잘못된 내용이 조용히 저장될 수 있으므로,
        # 안 맞으면 이 항목은 처리하지 않고 self_check가 저장을 막게 함
        return []

    plans = []
    for rp in rects_for_range(full_text, spans, f.start, f.end):
        rect = fitz.Rect(*rp.rect)
        plans.append((rp.page_index, rect, masked[rp.seg_lo:rp.seg_hi], rp.baseline, rp.fontsize, rp.rotate))
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

    plans_by_page: dict[int, list[tuple[fitz.Rect, str, tuple[float, float], float, int]]] = {}
    mapped: list[Finding] = []
    unmapped: list[Finding] = []
    for f in approved:
        plans = _plan_for_finding(f, full_text, spans)
        if not plans:
            unmapped.append(f)
            continue
        mapped.append(f)
        for page_index, rect, masked_segment, baseline, size, rotate in plans:
            plans_by_page.setdefault(page_index, []).append((rect, masked_segment, baseline, size, rotate))

    for page_index, plans in plans_by_page.items():
        page = doc[page_index]
        for rect, _masked_segment, _baseline, _size, _rotate in plans:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()

        for _rect, masked_segment, baseline, size, rotate in plans:
            page.insert_text(baseline, masked_segment, fontname="korea", fontsize=size, rotate=rotate)

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

    mapped, unmapped = apply_masking(doc, findings)
    # mapped: 리댁션을 시도한 항목 -> self_check로 실제로 지워졌는지 재확인
    # unmapped: 위치 자체를 못 찾아 손도 못 댄 항목 -> self_check로는 구분이
    # 안 되므로(그 자리에 다른 내용이 있으면 "지워짐"과 똑같이 보임) 무조건 실패 처리
    hidden_masked_values = scrub_hidden_content(doc)
    all_original_values = [f.value for f in mapped] + hidden_masked_values

    leftover = self_check(doc, mapped) + [f.value for f in unmapped]
    leftover += hidden_content_leftover(doc)

    if leftover:
        # 6.5.1: 재검증 실패 -> 저장 자체를 하지 않음, 원본/입력 파일 무변경
        doc.close()
        return MaskResult(False, None, {}, leftover, rotated_warning, hidden_warning)

    scrub_metadata(doc)

    # 6.5.2: 임시 파일에 먼저 쓰고, 검증 통과 후에만 최종 파일로 원자적 교체.
    # garbage=4: 리댁션으로 삭제된 주석 등이 남긴 참조 끊긴(orphan) 객체를 실제로 제거.
    # 이게 없으면 참조는 끊겨도 원본 텍스트 바이트가 파일에 그대로 남을 수 있음(실측 확인).
    # doc.save()가 실패하면(디스크 공간 부족 등) doc이 안 닫히고 tmp_path에 불완전한
    # 파일이 남을 수 있었음 -> 6.5.2가 요구하는 "성공/실패 불문 정리" 원칙에 맞게 실패
    # 시에도 doc을 닫고 남은 tmp 파일을 지운 뒤 예외를 그대로 전파
    tmp_path = output_path + ".tmp"
    try:
        doc.save(tmp_path, garbage=4, deflate=True)
    except Exception:
        doc.close()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
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
