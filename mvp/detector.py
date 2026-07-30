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


@dataclass
class Finding:
    type: str            # 이름 | 전화번호 | 이메일 | 계좌번호 | 주민등록번호 | 여권번호 | 주소
    value: str
    start: int
    end: int
    group: str = "기본"       # 기본 | 업무상성명후보
    approved: bool = True     # 검토 화면 기본값: 전체 승인 (6.3)

    def __repr__(self):
        return f"Finding({self.type!r}, {self.value!r}, [{self.start}:{self.end}], group={self.group!r})"


def _rrn_checksum_valid(rrn: str) -> bool:
    digits = rrn.replace("-", "")
    if len(digits) != 13 or not digits.isdigit():
        return False
    weights = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    total = sum(int(d) * w for d, w in zip(digits[:12], weights))
    check = (11 - (total % 11)) % 10
    return check == int(digits[12])


def detect_phone(text: str) -> list[Finding]:
    return [Finding("전화번호", m.group(), m.start(), m.end()) for m in PHONE_RE.finditer(text)]


def detect_email(text: str) -> list[Finding]:
    return [Finding("이메일", m.group(), m.start(), m.end()) for m in EMAIL_RE.finditer(text)]


def detect_rrn(text: str) -> list[Finding]:
    # 체크섬 불일치인 건 실제 주민번호가 아닐 가능성이 높아 제외 (오탐 축소, 8번 리스크 항목)
    return [Finding("주민등록번호", m.group(), m.start(), m.end())
            for m in RRN_RE.finditer(text) if _rrn_checksum_valid(m.group())]


def detect_passport(text: str) -> list[Finding]:
    return [Finding("여권번호", m.group(), m.start(), m.end()) for m in PASSPORT_RE.finditer(text)]


def detect_account(text: str) -> list[Finding]:
    out = []
    for m in ACCOUNT_RE.finditer(text):
        window = text[max(0, m.start() - 15):m.start()]
        if "계좌" in window or "입금" in window or "예금주" in window:
            out.append(Finding("계좌번호", m.group(), m.start(), m.end()))
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
            out.append(Finding("이름", word, start, end, group="기본"))
            seen.add((start, end))

    for start, end, word in _find_after_labels(text, BUSINESS_LABELS):
        if _is_surname_word(word) and (start, end) not in seen:
            out.append(Finding("이름", word, start, end, group="업무상성명후보"))
            seen.add((start, end))

    # "직위+이름" 패턴 (예: "감사팀장 홍길동")
    title_pattern = re.compile("(" + "|".join(BUSINESS_TITLES) + r")\s+([가-힣]{2,4})")
    for m in title_pattern.finditer(text):
        start, end, word = m.start(2), m.end(2), m.group(2)
        if _is_surname_word(word) and (start, end) not in seen:
            out.append(Finding("이름", word, start, end, group="업무상성명후보"))
            seen.add((start, end))

    return out


def detect_addresses(text: str) -> list[Finding]:
    out = []
    region_pattern = re.compile("|".join(re.escape(r) for r in sorted(REGIONS, key=len, reverse=True)))
    for m in region_pattern.finditer(text):
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        addr = text[m.start():line_end].strip()
        if len(addr) > len(m.group()):  # 지역명 단독은 주소로 안 봄
            out.append(Finding("주소", addr, m.start(), m.start() + len(addr)))
    return out


def detect_all(text: str) -> list[Finding]:
    findings: list[Finding] = []
    findings += detect_phone(text)
    findings += detect_email(text)
    findings += detect_rrn(text)
    findings += detect_passport(text)
    findings += detect_account(text)
    findings += detect_names(text)
    findings += detect_addresses(text)
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
        "기안자: 이감사 / 검토: 박팀장 / 결재: 최과장\n"
    )
    for f in detect_all(sample):
        print(f)
