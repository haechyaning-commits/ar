"""
template_fingerprint.py (6.2.2 문서 템플릿 지문 인식) 스트레스 테스트.

핵심 시나리오: 성씨사전에 없는 이름이라 detector.py가 놓치는 경우, 이미 같은
양식으로 저장된 템플릿 지문이 그 위치를 우선 확인 후보로 되살려주는지 실제
PDF로 실측 확인한다.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz

import template_fingerprint as tf
from detector import detect_all
from pdf_extract import extract_text_and_spans, page_sizes

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def _new_tmp_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def _build_doc(tmp: Path, name: str, lines: list[tuple[tuple[float, float], str]]) -> Path:
    path = tmp / name
    doc = fitz.open()
    page = doc.new_page()
    for (x, y), text in lines:
        page.insert_text((x, y), text, fontsize=11, fontname="korea")
    doc.save(path)
    doc.close()
    return path


def _extract(path: Path):
    doc = fitz.open(path)
    full_text, spans = extract_text_and_spans(doc)
    sizes = page_sizes(doc)
    doc.close()
    return full_text, spans, sizes


# ---------------------------------------------------------------------------
def test_signature_similarity_same_vs_different_layout():
    print("test_signature_similarity_same_vs_different_layout")
    tmp = _new_tmp_dir("tf_sig_")

    doc_a = _build_doc(tmp, "a.pdf", [
        ((72, 72), "성명: 홍길동"),
        ((72, 100), "전화번호: 010-1111-2222"),
    ])
    doc_b = _build_doc(tmp, "b.pdf", [
        ((72, 72), "성명: 김철수"),
        ((72, 100), "전화번호: 010-9999-8888"),
    ])
    doc_c = _build_doc(tmp, "c.pdf", [
        ((300, 500), "주소: 서울특별시 강남구 테헤란로 1"),
    ])

    text_a, spans_a, sizes_a = _extract(doc_a)
    text_b, spans_b, sizes_b = _extract(doc_b)
    text_c, spans_c, sizes_c = _extract(doc_c)

    sig_a = tf.build_signature(text_a, spans_a, sizes_a)
    sig_b = tf.build_signature(text_b, spans_b, sizes_b)
    sig_c = tf.build_signature(text_c, spans_c, sizes_c)

    check("같은 양식(라벨 위치 동일, 값만 다름)은 앵커가 그대로 겹침",
          sig_a.anchors == sig_b.anchors, str(sig_a.anchors ^ sig_b.anchors))
    check("완전히 다른 양식은 유사도가 낮아 매칭 임계값 미만",
          tf._similarity(sig_a, sig_c) < tf.MATCH_THRESHOLD, str(tf._similarity(sig_a, sig_c)))


def test_record_then_suggest_recovers_missed_name():
    print("test_record_then_suggest_recovers_missed_name")
    tmp = _new_tmp_dir("tf_roundtrip_")
    data_dir = tmp / "data"

    # 1번 문서: "홍"은 성씨사전에 있어 detect_all이 정상적으로 이름을 잡음 -> 승인 후 지문 기록
    doc1 = _build_doc(tmp, "doc1.pdf", [
        ((72, 72), "성명: 홍길동"),
        ((72, 100), "전화번호: 010-1111-2222"),
    ])
    text1, spans1, sizes1 = _extract(doc1)
    findings1 = detect_all(text1)
    check("1번 문서: 이름이 정상 탐지됨(사전에 있는 성씨)",
          any(f.type == "이름" and f.value == "홍길동" for f in findings1), str(findings1))
    tf.record_template(text1, spans1, sizes1, findings1, data_dir=data_dir)

    stored = tf._load(data_dir)
    check("템플릿 지문이 1개 저장됨", len(stored) == 1, str(stored))

    # 2번 문서: 같은 양식, 같은 위치지만 "짜장면"은 성씨사전에 없어 detect_all이 이름을 놓침
    doc2 = _build_doc(tmp, "doc2.pdf", [
        ((72, 72), "성명: 짜장면"),
        ((72, 100), "전화번호: 010-3333-4444"),
    ])
    text2, spans2, sizes2 = _extract(doc2)
    findings2 = detect_all(text2)
    check("2번 문서: detect_all은 이름을 놓침(성씨사전에 없는 이름)",
          not any(f.type == "이름" for f in findings2), str(findings2))
    check("2번 문서: 전화번호는 정상 탐지됨(정규식 기반이라 사전과 무관)",
          any(f.type == "전화번호" for f in findings2), str(findings2))

    suggestions = tf.suggest_candidates(text2, spans2, sizes2, findings2, data_dir=data_dir)
    name_suggestions = [f for f in suggestions if f.type == "이름"]
    check("템플릿 지문이 놓친 이름 위치를 후보로 되살림",
          len(name_suggestions) == 1 and name_suggestions[0].value == "짜장면", str(suggestions))
    check("후보는 '템플릿후보' 그룹, 낮음 확신도로 표시됨",
          name_suggestions and name_suggestions[0].group == "템플릿후보"
          and name_suggestions[0].confidence == "낮음", str(name_suggestions))
    check("이미 탐지된 전화번호는 중복 후보로 제시되지 않음",
          not any(f.type == "전화번호" for f in suggestions), str(suggestions))


def test_no_suggestion_without_matching_template():
    print("test_no_suggestion_without_matching_template")
    tmp = _new_tmp_dir("tf_nomatch_")
    data_dir = tmp / "data"  # 아무 것도 기록되지 않은 빈 저장소

    doc = _build_doc(tmp, "doc.pdf", [((72, 72), "성명: 짜장면")])
    text, spans, sizes = _extract(doc)
    findings = detect_all(text)
    suggestions = tf.suggest_candidates(text, spans, sizes, findings, data_dir=data_dir)
    check("저장된 템플릿이 없으면 후보를 만들지 않음", suggestions == [], str(suggestions))


def test_merge_updates_existing_template():
    print("test_merge_updates_existing_template")
    tmp = _new_tmp_dir("tf_merge_")
    data_dir = tmp / "data"

    doc1 = _build_doc(tmp, "doc1.pdf", [
        ((72, 72), "성명: 홍길동"),
        ((72, 100), "전화번호: 010-1111-2222"),
    ])
    text1, spans1, sizes1 = _extract(doc1)
    tf.record_template(text1, spans1, sizes1, detect_all(text1), data_dir=data_dir)

    doc2 = _build_doc(tmp, "doc2.pdf", [
        ((72, 72), "성명: 김철수"),
        ((72, 100), "전화번호: 010-2222-3333"),
        ((72, 140), "이메일: test@example.com"),
    ])
    text2, spans2, sizes2 = _extract(doc2)
    tf.record_template(text2, spans2, sizes2, detect_all(text2), data_dir=data_dir)

    stored = tf._load(data_dir)
    check("같은 양식으로 판별돼 새 템플릿이 아니라 기존 템플릿에 병합됨",
          len(stored) == 1, str(stored))
    check("병합 후 표본 수가 증가함", stored[0]["sample_count"] == 2, str(stored))
    types_recorded = {it["type"] for it in stored[0]["items"]}
    check("새 문서에서 추가된 이메일 필드도 지문 항목에 합쳐짐",
          "이메일" in types_recorded, str(stored[0]["items"]))


def main():
    tests = [
        test_signature_similarity_same_vs_different_layout,
        test_record_then_suggest_recovers_missed_name,
        test_no_suggestion_without_matching_template,
        test_merge_updates_existing_template,
    ]
    for t in tests:
        t()

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 template_fingerprint 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
