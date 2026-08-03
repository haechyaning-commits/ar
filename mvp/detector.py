"""
개인정보 탐지 엔진.

정규식 기반: 전화번호/이메일/계좌번호/주민등록번호/여권번호
사전+문맥규칙 기반: 이름/주소

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def _load_lines(filename: str) -> set[str]:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


SURNAMES = _load_lines("surnames.txt")
EXCLUDE_WORDS = _load_lines("exclude_words.txt")
REGIONS = _load_lines("regions.txt")
SURNAMES_SORTED = sorted(SURNAMES, key=len, reverse=True)

BUSINESS_TITLES = [
    "팀장", "과장", "차장", "부장", "국장", "실장", "본부장", "이사",
    "계장", "반장", "주임", "대리", "사원", "감사관", "위원",
]

# "기안/검토/결재/협조"는 결재선 위치라 업무상 성명 그룹으로만 분류 (기본 마스킹 대상인 건 동일)
NAME_LABELS = ["성명", "신청인", "수령인", "예금주", "담당", "민원인", "가입자"]
BUSINESS_LABELS = ["기안자", "검토자", "결재자", "협조자", "작성자", "기안", "검토", "결재", "협조"]

HANGUL_RUN_RE = re.compile(r"[가-힣]{2,4}")

PHONE_RE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RRN_RE = re.compile(r"\d{6}-\d{7}")
PASSPORT_RE = re.compile(r"\b[A-Z]\d{8}\b")
# 계좌번호는 은행마다 자릿수가 달라 패턴만으론 오탐이 많음 -> "계좌" 문맥 근처에서만 인정
ACCOUNT_RE = re.compile(r"\d{2,6}-\d{1,6}-\d{2,8}")


# 6.3.5 저신뢰 항목 우선 정렬 기준. 탐지 방식별 기본 확신도(낮음/중간/높음).
CONFIDENCE_LEVELS = ["낮음", "중간", "높음"]


@dataclass
class Finding:
    type: str            # 이름 | 전화번호 | 이메일 | 계좌번호 | 주민등록번호 | 여권번호 | 주소
    value: str
    start: int
    end: int
    group: str = "기본"       # 기본 | 업무상성명후보 | 교차검증후보
    approved: bool = True     # 검토 화면 기본값: 전체 승인 (6.3)
    confidence: str = "중간"  # 낮음 | 중간 | 높음 (6.3.5 정렬 기준, 6.2.3 교차검증으로 조정됨)
    cross_validated: bool = False  # 6.2.3 교차검증 규칙으로 확신도가 상향/신규 추가된 항목

    def __repr__(self):
        return (f"Finding({self.type!r}, {self.value!r}, [{self.start}:{self.end}], "
                f"group={self.group!r}, confidence={self.confidence!r})")


def _rrn_checksum_valid(rrn: str) -> bool:
    digits = rrn.replace("-", "")
    if len(digits) != 13 or not digits.isdigit():
        return False
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    total = sum(int(d) * w for d, w in zip(digits[:12], weights))
    check = (11 - (total % 11)) % 10
    return check == int(digits[12])


def detect_phone(text: str) -> list[Finding]:
    return [Finding("전화번호", m.group(), m.start(), m.end(), confidence="높음")
            for m in PHONE_RE.finditer(text)]


def detect_email(text: str) -> list[Finding]:
    return [Finding("이메일", m.group(), m.start(), m.end(), confidence="높음")
            for m in EMAIL_RE.finditer(text)]


def detect_rrn(text: str) -> list[Finding]:
    # 체크섬 불일치인 건 실제 주민번호가 아닐 가능성이 높아 제외 (오탐 축소, 8번 리스크 항목)
    return [Finding("주민등록번호", m.group(), m.start(), m.end(), confidence="높음")
            for m in RRN_RE.finditer(text) if _rrn_checksum_valid(m.group())]


def detect_passport(text: str) -> list[Finding]:
    return [Finding("여권번호", m.group(), m.start(), m.end(), confidence="높음")
            for m in PASSPORT_RE.finditer(text)]


def detect_account(text: str) -> list[Finding]:
    out = []
    for m in ACCOUNT_RE.finditer(text):
        # 문맥 검색은 같은 줄 안에서만 (버그: 이전엔 줄바꿈을 넘어 이전 줄의
        # 무관한 단어까지 "계좌" 문맥으로 잘못 인정했음)
        line_start = text.rfind("\n", 0, m.start()) + 1
        window_start = max(line_start, m.start() - 15)
        window = text[window_start:m.start()]
        if "계좌" in window or "입금" in window or "예금주" in window:
            out.append(Finding("계좌번호", m.group(), m.start(), m.end(), confidence="높음"))
    return out


def _find_after_labels(text: str, labels: list[str]) -> list[tuple[int, int, str]]:
    """'라벨: 값' 패턴에서 라벨 뒤 첫 한글 토큰(2~4자)의 (start, end, word)를 찾음."""
    pattern = re.compile("(" + "|".join(sorted(map(re.escape, labels), key=len, reverse=True)) + r")\s*[:：]\s*")
    out = []
    for lm in pattern.finditer(text):
        rest = text[lm.end():lm.end() + 10]
        wm = HANGUL_RUN_RE.match(rest)
        if wm:
            out.append((lm.end() + wm.start(), lm.end() + wm.end(), wm.group()))
    return out


def _is_surname_word(word: str) -> bool:
    return word not in EXCLUDE_WORDS and any(word.startswith(s) for s in SURNAMES_SORTED)


def detect_names(text: str) -> list[Finding]:
    out: list[Finding] = []
    seen: set[tuple[int, int]] = set()

    for start, end, word in _find_after_labels(text, NAME_LABELS):
        if _is_surname_word(word) and (start, end) not in seen:
            out.append(Finding("이름", word, start, end, group="기본", confidence="중간"))
            seen.add((start, end))

    for start, end, word in _find_after_labels(text, BUSINESS_LABELS):
        if _is_surname_word(word) and (start, end) not in seen:
            out.append(Finding("이름", word, start, end, group="업무상성명후보", confidence="낮음"))
            seen.add((start, end))

    # "직위+이름" 패턴 (예: "감사팀장 홍길동"). 공백/탭만 허용 -- \s+였을 때
    # 줄바꿈까지 걸쳐 매칭되어 다음 줄의 무관한 단어(예: "...최과장\n연락처")를
    # 이름으로 잘못 잡는 버그가 있었음 (같은 줄 안에서만 성립하는 패턴이므로 수정)
    title_pattern = re.compile("(" + "|".join(BUSINESS_TITLES) + r")[^\S\n]+([가-힣]{2,4})")
    for m in title_pattern.finditer(text):
        start, end, word = m.start(2), m.end(2), m.group(2)
        if _is_surname_word(word) and (start, end) not in seen:
            out.append(Finding("이름", word, start, end, group="업무상성명후보", confidence="낮음"))
            seen.add((start, end))

    return out


ADDRESS_LABELS = ["주소", "거주지"]
_ADDRESS_LABEL_RE = re.compile("(" + "|".join(ADDRESS_LABELS) + r")\s*[:：]\s*")


def detect_addresses(text: str) -> list[Finding]:
    # 설계서 6.2: "행정구역 사전 매칭 + 라벨 문맥 규칙"을 함께 써야 함.
    # 이전엔 라벨 없이 문서 전체에서 지역명만으로 매칭해서, 지역명이 들어간
    # 아무 문장(예: "워크숍은 서울특별시에서 개최되었습니다")의 나머지 줄까지
    # 전부 "주소"로 오탐하는 버그가 있었음 -> "주소:"/"거주지:" 라벨 뒤로 한정
    out = []
    region_pattern = re.compile("|".join(re.escape(r) for r in sorted(REGIONS, key=len, reverse=True)))
    for lm in _ADDRESS_LABEL_RE.finditer(text):
        line_end = text.find("\n", lm.end())
        if line_end == -1:
            line_end = len(text)
        raw = text[lm.end():line_end]
        value = raw.strip()
        if not value or not region_pattern.match(value):
            continue
        start = lm.end() + raw.find(value)
        out.append(Finding("주소", value, start, start + len(value), confidence="낮음"))
    return out


def _line_span(text: str, pos: int) -> tuple[int, int]:
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    return line_start, line_end


def _bump_confidence(f: Finding) -> None:
    idx = CONFIDENCE_LEVELS.index(f.confidence)
    if idx < len(CONFIDENCE_LEVELS) - 1:
        f.confidence = CONFIDENCE_LEVELS[idx + 1]
    f.cross_validated = True


# 6.2.3 규칙1: 이 라벨이 전화번호 앞에 있으면 같은 줄의 이름 탐지 확신도를 상향
PHONE_CONTEXT_LABELS = ["발신자", "연락처"]
_PHONE_LABEL_RE = re.compile("(" + "|".join(PHONE_CONTEXT_LABELS) + r")\s*[:：]")
# 6.2.3 규칙2: 반복되는 라벨 패턴에서 놓친 위치를 우선 탐지 후보로 격상
_NAME_LABEL_RE = re.compile(r"성명\s*[:：]\s*")


def apply_cross_validation(text: str, findings: list[Finding]) -> list[Finding]:
    """6.2.3 교차검증 규칙: 서로 다른 탐지 결과끼리 문맥으로 서로의 확신도를 보강.

    규칙1: 전화번호 주변에 "발신자:"/"연락처:" 라벨이 있으면, 같은 줄에 있는
           이름 탐지 항목의 확신도를 한 단계 상향(높음 이상 정보인 전화번호가
           라벨로 확정됐으므로, 같은 줄의 이름도 맞을 확률이 높다고 봄).
    규칙2: "성명:" 라벨 뒤에 이미 확정된 이름이 있고, 문서 내 같은 라벨 패턴이
           반복되는데 이름이 탐지되지 않은 위치가 있으면(예: 성씨사전에 없는
           이름), 그 위치를 낮은 확신도의 우선 탐지 후보로 새로 추가.
    """
    findings = list(findings)

    # 규칙 1
    for f in findings:
        if f.type != "전화번호":
            continue
        line_start, line_end = _line_span(text, f.start)
        if _PHONE_LABEL_RE.search(text[line_start:f.start]):
            for other in findings:
                if other.type == "이름" and line_start <= other.start < line_end:
                    _bump_confidence(other)

    # 규칙 2
    label_ends = [lm.end() for lm in _NAME_LABEL_RE.finditer(text)]
    confirmed_starts = {f.start for f in findings if f.type == "이름"}
    has_confirmed_repeat = any(e in confirmed_starts for e in label_ends)
    if has_confirmed_repeat:
        for e in label_ends:
            if e in confirmed_starts:
                continue
            wm = HANGUL_RUN_RE.match(text[e:e + 10])
            if wm:
                findings.append(Finding(
                    "이름", wm.group(), e + wm.start(), e + wm.end(),
                    group="교차검증후보", confidence="낮음", cross_validated=True,
                ))

    return findings


def _dedupe(findings: list[Finding]) -> list[Finding]:
    # 같은 위치(start/end)가 서로 다른 규칙에 동시에 걸리는 경우가 있음
    # (예: "계좌" 라벨 근처의 전화번호가 전화번호+계좌번호로 이중 탐지됨) -> 하나만 남김
    seen_spans: set[tuple[int, int]] = set()
    deduped: list[Finding] = []
    for f in findings:
        key = (f.start, f.end)
        if key in seen_spans:
            continue
        seen_spans.add(key)
        deduped.append(f)
    return deduped


def detect_all(text: str) -> list[Finding]:
    findings: list[Finding] = []
    findings += detect_phone(text)
    findings += detect_email(text)
    findings += detect_rrn(text)
    findings += detect_passport(text)
    findings += detect_account(text)
    findings += detect_names(text)
    findings += detect_addresses(text)

    findings = _dedupe(findings)
    findings = apply_cross_validation(text, findings)
    findings = _dedupe(findings)

    findings.sort(key=lambda f: f.start)
    return findings


if __name__ == "__main__":
    sample = (
        "지출결의서\n"
        "신청인: 김테스트 (010-1234-5678)\n"
        "주민등록번호: 900101-1234568\n"
        "입금계좌: 국민은행 123456-04-789012 (예금주: 김테스트)\n"
        "이메일: test.dummy@example.com\n"
        "주소: 서울특별시 강남구 테헤란로 123\n"
        "연락처: 010-9999-8888 (담당: 최민수)\n"  # 규칙1: 라벨+전화번호 옆 이름 확신도 상향 확인용
        "성명: 김철수\n"
        "성명: 삼식이\n"  # 성씨사전에 없는 더미 이름 -> 규칙2로 우선 탐지 후보 격상 확인용
        "기안자: 이감사 / 검토: 박팀장 / 결재: 최과장\n"
    )
    for f in detect_all(sample):
        print(f)
