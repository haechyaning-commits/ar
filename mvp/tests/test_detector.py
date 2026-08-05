"""
detector.py 엣지케이스 스트레스 테스트.

오탐/누락 가능성이 있는 지점을 직접 실행해서 실측 확인한다 (추측이 아니라 실행 결과 기준).
실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detector import detect_all, find_occurrences, _rrn_checksum_valid, PASSPORT_RE, PHONE_RE

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def types_at(text, wanted_type):
    return [f.value for f in detect_all(text) if f.type == wanted_type]


# ---------------------------------------------------------------------------
def test_rrn_checksum():
    print("test_rrn_checksum")
    # 900101-1234568은 기존 더미 데이터에서 쓰던 값 -> 체크섬 유효
    check("체크섬 유효한 주민번호는 탐지됨", _rrn_checksum_valid("900101-1234568"))
    valid = types_at("주민등록번호: 900101-1234568", "주민등록번호")
    check("체크섬 유효 -> detect_all에서 탐지", valid == ["900101-1234568"], str(valid))

    # 마지막 자리만 바꿔 체크섬을 깨뜨림
    invalid = types_at("주민등록번호: 900101-1234569", "주민등록번호")
    check("체크섬 무효 -> detect_all에서 제외(오탐 축소)", invalid == [], str(invalid))


def test_phone_formats():
    print("test_phone_formats")
    cases = {
        "010-1234-5678": True,
        "01012345678": True,
        "011-123-4567": True,   # 3자리 국번 구형 포맷
        "016-1234-5678": True,
        "019-1234-5678": True,
        "015-1234-5678": False,  # 실제 존재하지 않는 통신사 접두 -> 오탐 아님이 맞음
        "02-1234-5678": False,   # 유선전화는 범위 밖(설계상 휴대폰만)
    }
    for value, expected in cases.items():
        m = PHONE_RE.search(value)
        found = bool(m and m.group() == value)
        check(f"'{value}' 전화번호 탐지={expected}", found == expected, f"got match={m.group() if m else None}")


def test_passport_boundary():
    print("test_passport_boundary")
    m = PASSPORT_RE.search("여권번호: M12345678 입니다")
    check("공백으로 구분된 여권번호는 탐지됨", bool(m and m.group() == "M12345678"))

    m_lower = PASSPORT_RE.search("여권번호: m12345678 입니다")
    check("소문자는 탐지 안 됨(설계상 대문자만) -- 실사용시 소문자 입력 케이스 존재하면 누락 위험",
          m_lower is None, "실측: 소문자 여권번호가 조금이라도 쓰이면 이 코드가 놓친다")

    # 한글과 공백 없이 바로 붙은 경우 -- 유니코드 정규식에서 한글도 \w로 취급되어
    # \b(단어 경계)가 성립하지 않을 수 있음 (실측 확인용, 참인지 실행해서 검증)
    m_touch = PASSPORT_RE.search("번호M12345678끝")
    print(f"    (참고) 한글에 바로 붙은 여권번호 매칭 여부: {bool(m_touch)}")


def test_account_context_window():
    print("test_account_context_window")
    near = types_at("계좌: 123456-04-789012", "계좌번호")
    check("라벨이 가까이(15자 이내) 있으면 탐지됨", near == ["123456-04-789012"], str(near))

    # "계좌" 라벨이 숫자로부터 15자보다 먼 경우 -- 코드의 윈도우 제한(m.start()-15) 실측
    # (다른 트리거 단어와 안 겹치도록 순수 공백으로만 거리를 벌림)
    far_label_text = "계좌" + (" " * 20) + "123456-04-789012"
    far = types_at(far_label_text, "계좌번호")
    gap = far_label_text.index("123456") - far_label_text.index("계좌")
    check(f"라벨이 {gap}자(15자 초과) 떨어지면 윈도우 제한으로 탐지 실패 (실사용 문장 길이에 따라 실누락 위험)",
          far == [], str(far))

    cross_line = "계좌번호\n123456-04-789012"
    cross = types_at(cross_line, "계좌번호")
    check("라벨이 이전 줄에 있으면 탐지 안 됨 (설계 의도: 같은 줄 내에서만 문맥 인정)",
          cross == [], str(cross))


def test_phone_shaped_account_number_gets_mislabeled_as_phone():
    print("test_phone_shaped_account_number_gets_mislabeled_as_phone")
    # 010-1234-5678 형태는 전화번호 정규식과 계좌번호 정규식 둘 다에 매칭됨.
    # detect_all은 같은 span을 detect_phone이 먼저 채워서 "전화번호"로만 분류함 (코드 주석에 명시된 트레이드오프)
    text = "계좌: 010-1234-5678"
    findings = detect_all(text)
    types = [(f.type, f.value) for f in findings]
    check("전화번호 모양 값은 '계좌' 문맥이어도 전화번호로만 분류됨 (알려진 트레이드오프, 버그 아님)",
          types == [("전화번호", "010-1234-5678")], str(types))


def test_name_exclude_words_prevent_false_positive():
    print("test_name_exclude_words_prevent_false_positive")
    result = types_at("담당: 국민은행", "이름")
    check("제외어 사전 단어(부서/은행명)는 이름으로 오탐되지 않음", result == [], str(result))

    result2 = types_at("담당: 감사팀", "이름")
    check("부서명(감사팀)도 이름으로 오탐되지 않음", result2 == [], str(result2))


def test_compound_surname():
    print("test_compound_surname")
    result = types_at("성명: 남궁민지", "이름")
    check("복성(남궁)도 이름으로 탐지됨", result == ["남궁민지"], str(result))


def test_surname_dictionary_confirmed_source_coverage():
    """9번 표 정책 확정(v27): 성씨 사전 출처를 통계청 2015년 인구주택총조사
    성씨·본관 조사(공공데이터포털/SGIS 공개자료)로 확정하고, 그 기준 인구
    상위 100위 안에 있었으나 기존 목록에 빠져 있던 성씨를 추가함(류/나/탁/
    국/어/은/편). 실제로 사전에 반영돼 탐지되는지 확인."""
    print("test_surname_dictionary_confirmed_source_coverage")
    for surname, given_name in [
        ("류", "지훈"), ("나", "현우"), ("탁", "민수"),
        ("국", "은비"), ("어", "진영"), ("은", "성민"), ("편", "가은"),
    ]:
        result = types_at(f"성명: {surname}{given_name}", "이름")
        check(f"census 추가 성씨 '{surname}'로 시작하는 이름이 탐지됨",
              result == [f"{surname}{given_name}"], str(result))


def test_business_title_pattern_no_space_before_title():
    print("test_business_title_pattern_no_space_before_title")
    findings = detect_all("감사팀장 홍길동 배석")
    names = [(f.value, f.group) for f in findings if f.type == "이름"]
    check("'감사팀장 홍길동'에서 이름만 정확히 추출되고 업무상성명후보로 분류됨",
          names == [("홍길동", "업무상성명후보")], str(names))


def test_address_region_alone_not_flagged():
    print("test_address_region_alone_not_flagged")
    result = types_at("주소: 서울", "주소")
    check("지역명 단독은 주소로 탐지 안 됨", result == [], str(result))

    result2 = types_at("주소: 서울특별시 강남구 테헤란로 123", "주소")
    check("지역명+상세주소는 탐지됨", len(result2) == 1 and result2[0].startswith("서울특별시"), str(result2))


def test_duplicate_name_occurrences_both_flagged():
    print("test_duplicate_name_occurrences_both_flagged")
    text = "신청인: 김테스트\n예금주: 김테스트"
    findings = [f for f in detect_all(text) if f.type == "이름"]
    check("동일 이름이 두 번 나오면 두 건 모두 별도 탐지됨(같은 문자열이어도 위치가 다르면 각각 마스킹 대상)",
          len(findings) == 2, str(findings))
    check("두 항목의 위치(span)가 서로 다름", findings[0].start != findings[1].start)


def test_find_occurrences_exact_match_default():
    print("test_find_occurrences_exact_match_default")
    text = "신청인: 김철수\n비고: 김 철수 확인 요망"
    exact_span = (text.index("김철수"), text.index("김철수") + len("김철수"))
    check("기본값(완전일치)은 공백이 낀 표기 변형을 찾지 못하고 정확히 일치하는 곳만 찾음",
          find_occurrences(text, "김철수") == [exact_span], str(find_occurrences(text, "김철수")))


def test_find_occurrences_loose_match_ignores_inner_whitespace():
    print("test_find_occurrences_loose_match_ignores_inner_whitespace")
    text = "신청인: 김철수\n비고: 김  철수 확인 요망\n담당: 최영희"
    occurrences = find_occurrences(text, "김철수", loose=True)
    matched_texts = [text[s:e] for s, e in occurrences]
    check("부분일치 옵션은 완전일치 값도 그대로 포함", "김철수" in matched_texts, str(matched_texts))
    check("공백이 섞인 표기 변형('김  철수')도 찾아냄", "김  철수" in matched_texts, str(matched_texts))
    check("전혀 다른 이름(최영희)은 걸리지 않음", not any("최영희" in t for t in matched_texts), str(matched_texts))


def test_find_occurrences_loose_match_does_not_allow_reordering():
    print("test_find_occurrences_loose_match_does_not_allow_reordering")
    text = "수철김 (순서가 다른 글자, 매칭되면 안 됨)"
    check("부분일치도 글자 순서가 바뀐 것까지는 허용하지 않음(오탐 위험 제한)",
          find_occurrences(text, "김철수", loose=True) == [])


def main():
    tests = [
        test_rrn_checksum,
        test_phone_formats,
        test_passport_boundary,
        test_account_context_window,
        test_phone_shaped_account_number_gets_mislabeled_as_phone,
        test_name_exclude_words_prevent_false_positive,
        test_compound_surname,
        test_surname_dictionary_confirmed_source_coverage,
        test_business_title_pattern_no_space_before_title,
        test_address_region_alone_not_flagged,
        test_duplicate_name_occurrences_both_flagged,
        test_find_occurrences_exact_match_default,
        test_find_occurrences_loose_match_ignores_inner_whitespace,
        test_find_occurrences_loose_match_does_not_allow_reordering,
    ]
    for t in tests:
        t()

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 detector 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
