"""
이름/주소 탐지 정확도 측정 스크립트 (12.3 "이름/주소 탐지 정확도" 항목의 미검증 -> 실측 보완).

설계서(6.2)는 이름/주소를 "성씨/행정구역 사전 + 문맥 규칙"으로 탐지하며 화이트리스트가
없어 정확도가 정규식 항목(전화번호 등) 대비 구조적으로 낮을 수밖에 없다고 명시함.
이 스크립트는 그 한계가 "어느 정도인지"를 다양한(정상/경계/함정) 케이스로 구성한
더미 문서 코퍼스로 실측해 정밀도(precision)/재현율(recall)을 계산한다.

문서는 `{{이름}}` / `[[주소]]` 마커로 정답 구간을 표시하고, 마커를 제거한 평문에
detect_names/detect_addresses를 돌려 예측 구간과 비교한다.

전부 더미 데이터 (2.1 선행 조건).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from detector import detect_names, detect_addresses

_MARKER_RE = re.compile(r"\{\{(.*?)\}\}|\[\[(.*?)\]\]")


def parse_marked(marked_text: str) -> tuple[str, list[tuple[int, int, str, str]]]:
    """마커를 제거한 평문과 정답 (start, end, type, value) 목록을 반환."""
    plain_parts: list[str] = []
    gold: list[tuple[int, int, str, str]] = []
    last = 0
    consumed_len = 0
    for m in _MARKER_RE.finditer(marked_text):
        before = marked_text[last:m.start()]
        plain_parts.append(before)
        consumed_len += len(before)
        if m.group(1) is not None:
            value, typ = m.group(1), "이름"
        else:
            value, typ = m.group(2), "주소"
        start = consumed_len
        end = start + len(value)
        gold.append((start, end, typ, value))
        plain_parts.append(value)
        consumed_len = end
        last = m.end()
    plain_parts.append(marked_text[last:])
    return "".join(plain_parts), gold


@dataclass
class Case:
    label: str  # 케이스 성격 설명 (표준 / 경계 / 함정)
    marked_text: str


# ---------------------------------------------------------------------------
# 더미 테스트 코퍼스 (실제 감사파일 아님, 2.1 선행 조건)
# ---------------------------------------------------------------------------
CASES: list[Case] = [
    Case("표준 - 신청인/예금주 라벨", (
        "지출결의서\n"
        "신청인: {{김철수}} (010-1234-5678)\n"
        "입금계좌: 국민은행 123456-04-789012 (예금주: {{김철수}})\n"
        "주소: [[서울특별시 강남구 테헤란로 45]]\n"
    )),
    Case("표준 - 성명 라벨 + 도로명 주소", (
        "출장신청서\n"
        "성명: {{이영희}}\n"
        "주소: [[경기도 수원시 팔달구 매산로 12]]\n"
    )),
    Case("표준 - 민원인/수령인 라벨", (
        "민원처리결과\n"
        "민원인: {{정다은}}\n"
        "수령인: {{정다은}}\n"
    )),
    Case("표준 - 직위+이름 패턴(결재선)", (
        "감사팀장 {{홍길동}} 확인\n"
        "기안자: {{이감사}} / 검토: {{박팀장}} / 결재: {{최과장}}\n"
    )),
    Case("경계 - 아파트 동/호 주소", (
        "계약서\n"
        "가입자: {{강민준}}\n"
        "주소: [[부산광역시 해운대구 마린시티 105동 1502호]]\n"
    )),
    Case("경계 - 시/군/구 상세 행정구역 주소", (
        "출장신청서\n"
        "신청인: {{조은서}}\n"
        "주소: [[전라남도 순천시 조례동 123-4]]\n"
    )),
    Case("경계 - 짧은 2자 성명 + 4자 성명 혼합", (
        "지출결의서\n"
        "신청인: {{윤아}}\n"
        "예금주: {{서준서연}}\n"
    )),
    Case("함정(누락 유발) - 미지원 라벨 '이름:'", (
        # NAME_LABELS/BUSINESS_LABELS에 없는 라벨 -> 탐지 실패 예상(재현율 한계 실측용)
        "민원접수증\n"
        "이름: {{박민수}}\n"
    )),
    Case("함정(누락 유발) - 라벨 없이 표 형태로만 등장", (
        # 라벨 문맥 규칙에 안 걸리는 배치 -> 탐지 실패 예상
        "담당부서   처리결과   {{김도윤}}   완료\n"
    )),
    Case("함정(오탐 유발) - 지역명이 개인주소 아닌 기관 소재지 언급", (
        # detect_addresses는 라벨 없이 지역명+줄내 후속텍스트만으로 판단 -> 오탐 예상(정밀도 한계 실측용)
        # 실제로는 주소가 아니므로 정답(gold) 마커를 달지 않음 -> 탐지되면 그대로 오탐으로 집계됨
        "본 건은 서울특별시 소재 국세청 관할 사항으로 확인됨\n"
    )),
    Case("함정(오탐 유발) - 제외어 사전 미등재 부서명", (
        # "인천지사"처럼 exclude_words.txt에 없는 부서/기관명이 성씨 접두어로 오탐될 수 있음
        "협조자: {{서인천지사}}\n"  # "서"가 성씨 -> "서인천지사"를 이름으로 오탐할 가능성
    )),
    Case("경계 - 겹성(compound surname, 사전 미등재)", (
        # surnames.txt에 2자 복성(남궁/황보/선우 등)이 없어 탐지 실패 예상
        "성명: {{남궁민}}\n"
    )),
]


def evaluate() -> None:
    total_gold_name = total_gold_addr = 0
    total_pred_name = total_pred_addr = 0
    tp_name = tp_addr = 0

    print(f"{'케이스':45s} {'유형':6s} {'정답':>4s} {'예측':>4s} {'적중':>4s}  비고")
    print("-" * 100)

    for case in CASES:
        plain, gold = parse_marked(case.marked_text)
        gold_names = {(s, e) for s, e, t, v in gold if t == "이름"}
        gold_addrs = {(s, e) for s, e, t, v in gold if t == "주소"}

        pred_names = {(f.start, f.end) for f in detect_names(plain)}
        pred_addrs = {(f.start, f.end) for f in detect_addresses(plain)}

        hit_names = gold_names & pred_names
        hit_addrs = gold_addrs & pred_addrs

        total_gold_name += len(gold_names)
        total_gold_addr += len(gold_addrs)
        total_pred_name += len(pred_names)
        total_pred_addr += len(pred_addrs)
        tp_name += len(hit_names)
        tp_addr += len(hit_addrs)

        note_parts = []
        if len(gold_names) > len(hit_names):
            note_parts.append(f"이름 누락 {len(gold_names) - len(hit_names)}건")
        if len(pred_names) > len(hit_names):
            note_parts.append(f"이름 오탐 {len(pred_names) - len(hit_names)}건")
        if len(gold_addrs) > len(hit_addrs):
            note_parts.append(f"주소 누락 {len(gold_addrs) - len(hit_addrs)}건")
        if len(pred_addrs) > len(hit_addrs):
            note_parts.append(f"주소 오탐 {len(pred_addrs) - len(hit_addrs)}건")
        note = ", ".join(note_parts) if note_parts else "정상"

        if gold_names or pred_names:
            print(f"{case.label:45s} {'이름':6s} {len(gold_names):>4d} {len(pred_names):>4d} {len(hit_names):>4d}  {note}")
        if gold_addrs or pred_addrs:
            print(f"{case.label:45s} {'주소':6s} {len(gold_addrs):>4d} {len(pred_addrs):>4d} {len(hit_addrs):>4d}  {note}")

    def precision_recall(tp, total_pred, total_gold):
        precision = tp / total_pred if total_pred else float("nan")
        recall = tp / total_gold if total_gold else float("nan")
        return precision, recall

    p_name, r_name = precision_recall(tp_name, total_pred_name, total_gold_name)
    p_addr, r_addr = precision_recall(tp_addr, total_pred_addr, total_gold_addr)

    print("-" * 100)
    print(f"이름: precision={p_name:.0%} ({tp_name}/{total_pred_name})  recall={r_name:.0%} ({tp_name}/{total_gold_name})")
    print(f"주소: precision={p_addr:.0%} ({tp_addr}/{total_pred_addr})  recall={r_addr:.0%} ({tp_addr}/{total_gold_addr})")


if __name__ == "__main__":
    evaluate()
