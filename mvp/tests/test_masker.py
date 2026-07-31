"""
masker.py 스트레스 테스트 -- 리댁션/자체재검증/숨김콘텐츠/메타데이터/회전텍스트.

기본 케이스는 "정상 동작 확인"이고, 일부 케이스는 이번 테스트로 실측 확인된
알려진 문제를 회귀 테스트로 고정한 것이다 (해당 테스트는 주석에 ⚠로 표시).

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz

from detector import detect_all
from masker import mask_pdf, has_rotated_text, has_hidden_content

_FAILURES: list[str] = []


def _new_tmp() -> Path:
    # 테스트마다 독립된 임시 폴더를 새로 만든다 -- 하나의 전역 폴더를 여러 테스트가
    # 공유하면 fitz.Document 객체 수명(GC 타이밍)이 테스트 간에 겹치면서
    # "annotation not bound to any page" 같은 무관한 오류가 실측으로 발생함.
    return Path(tempfile.mkdtemp(prefix="masker_test_"))


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def _mask(doc_path: Path, out_path: Path):
    doc = fitz.open(doc_path)
    full_text = "\n".join(p.get_text(sort=True) for p in doc)
    findings = detect_all(full_text)
    doc.close()
    result = mask_pdf(str(doc_path), findings, str(out_path))
    return findings, result


# ---------------------------------------------------------------------------
def test_basic_roundtrip_byte_level_removal():
    print("test_basic_roundtrip_byte_level_removal")
    tmp = _new_tmp()
    src = tmp / "basic.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "신청인: 김테스트 (010-1234-5678)", fontsize=11, fontname="korea")
    page.insert_text((72, 100), "비고: 본 문서는 테스트용입니다", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    out = tmp / "basic_masked.pdf"
    findings, result = _mask(src, out)
    check("자체 재검증 통과", result.success, str(result.leftover))

    raw = open(out, "rb").read()
    check("원본 전화번호가 결과물 바이트에서 완전히 사라짐(오버레이 아님)", b"010-1234-5678" not in raw)

    masked_doc = fitz.open(out)
    masked_text = masked_doc[0].get_text(sort=True)
    masked_doc.close()
    check("비-PII 텍스트('비고: 본 문서는...')는 그대로 남아 추출 가능", "비고: 본 문서는" in masked_text, masked_text)


def test_metadata_scrubbed():
    print("test_metadata_scrubbed")
    tmp = _new_tmp()
    src = tmp / "meta.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "신청인: 김테스트", fontsize=11, fontname="korea")
    doc.set_metadata({"author": "김테스트", "title": "지출결의서_김테스트"})
    doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><a>김테스트</a></x:xmpmeta>')
    doc.save(src)
    doc.close()

    out = tmp / "meta_masked.pdf"
    findings, result = _mask(src, out)
    check("자체 재검증 통과", result.success)

    masked_doc = fitz.open(out)
    meta = masked_doc.metadata
    xml = masked_doc.get_xml_metadata()
    masked_doc.close()
    check("Info 딕셔너리(author/title) 비워짐", meta["author"] == "" and meta["title"] == "", str(meta))
    check("XMP 메타데이터 비워짐", xml == "", repr(xml))


def test_rotated_text_pii_removed_but_replacement_rendering_is_broken():
    print("test_rotated_text_pii_removed_but_replacement_rendering_is_broken")
    tmp = _new_tmp()
    src = tmp / "rotated.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "일반 텍스트 라인", fontsize=11, fontname="korea")
    page.insert_textbox(fitz.Rect(72, 150, 100, 400), "주민등록번호: 900101-1234568",
                         fontsize=11, fontname="korea", rotate=90)
    doc.save(src)
    doc.close()

    out = tmp / "rotated_masked.pdf"
    findings, result = _mask(src, out)
    check("회전 텍스트 경고 플래그가 True (설계서 6.5.1-4 완화책)", result.rotated_text_warning)
    check("이번 케이스는 sort=True 덕분에 탐지 자체는 됨 (RRN 1건)",
          len(findings) == 1 and findings[0].type == "주민등록번호", str(findings))
    check("자체 재검증은 통과(원문 숫자는 안 남음)", result.success, str(result.leftover))

    masked_doc = fitz.open(out)
    spans = [s for b in masked_doc[0].get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]
    masked_doc.close()
    mask_spans = [s for s in spans if "*" in s["text"]]
    # ⚠ 확인된 문제: 마스킹 대체 텍스트("******-*******")가 두 곳에 중복 삽입되고,
    # 둘 다 원본의 회전 방향(dir=(0,-1))을 무시한 채 가로로 삽입됨 -- PII 자체는
    # 지워지지만 결과물이 시각적으로 깨짐. apply_masking()이 line.get("dir")를
    # 반영하지 않고 항상 수평으로 insert_text 하기 때문 (masker.py _plan_replacements/apply_masking).
    check("⚠ 알려진 문제: 마스킹 텍스트가 중복 삽입됨 (기대: 1곳, 실제)",
          len(mask_spans) == 2, f"{len(mask_spans)}개 span: {[s['text'] for s in mask_spans]}")
    if mask_spans:
        check("⚠ 알려진 문제: 대체 텍스트가 원본 회전 방향을 무시하고 가로로 삽입됨",
              all(abs(s.get("dir", (1, 0))[1]) < 0.01 for s in mask_spans),
              "원래 라벨은 세로(dir=(0,-1))인데 마스킹 텍스트는 가로로 들어감")


def test_hidden_annotation_content_survives_unscrubbed():
    print("test_hidden_annotation_content_survives_unscrubbed")
    tmp = _new_tmp()
    src = tmp / "annot.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "지출결의서 신청인: 김테스트", fontsize=11, fontname="korea")
    page.add_text_annot((72, 150), "메모: 실제 연락처는 010-9999-8888 입니다")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    check("has_hidden_content가 주석 존재를 감지함", has_hidden_content(doc2))
    doc2.close()

    out = tmp / "annot_masked.pdf"
    findings, result = _mask(src, out)
    check("본문 탐지 결과에는 주석 속 전화번호가 없음(주석은 본문 텍스트 추출 범위 밖)",
          all(f.value != "010-9999-8888" for f in findings), str(findings))
    check("자체 재검증은 '성공'으로 보고함(주석을 스캔하지 않으므로)", result.success)
    check("hidden_content_warning 플래그는 True (경고는 뜸)", result.hidden_content_warning)

    masked_doc = fitz.open(out)
    annots = list(masked_doc[0].annots() or [])
    contents = [a.info.get("content", "") for a in annots]
    masked_doc.close()
    # ⚠ 확인된 문제(심각): 설계서 6.5.1-2는 "주석에서 PII 발견 시 제거"를 요구하지만
    # 현재 masker.py의 has_hidden_content()는 존재 여부만 boolean으로 반환할 뿐 실제로
    # 주석 내용을 스캔/제거하지 않는다. 그 결과 검토자가 승인한 적 없는 전화번호가
    # "재검증 통과"로 표시된 최종 파일에 그대로 남는다.
    check("⚠ 심각: 주석의 전화번호가 '마스킹 완료' 파일에 그대로 남아있음 (제거 안 됨)",
          len(annots) == 1 and "010-9999-8888" in contents[0], str(contents))


def test_hidden_embedded_file_survives_unscrubbed():
    print("test_hidden_embedded_file_survives_unscrubbed")
    tmp = _new_tmp()
    src = tmp / "embed.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "지출결의서 신청인: 김테스트", fontsize=11, fontname="korea")
    doc.embfile_add("memo.txt", "주민번호: 900101-1234568".encode("utf-8"), filename="memo.txt")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    check("has_hidden_content가 첨부파일 존재를 감지함", has_hidden_content(doc2))
    doc2.close()

    out = tmp / "embed_masked.pdf"
    findings, result = _mask(src, out)
    check("자체 재검증은 '성공'으로 보고함(첨부파일을 스캔하지 않으므로)", result.success)

    masked_doc = fitz.open(out)
    has_embed = masked_doc.embfile_count() > 0
    content = masked_doc.embfile_get(0).decode("utf-8") if has_embed else ""
    masked_doc.close()
    # ⚠ 확인된 문제(심각): 첨부파일도 주석과 마찬가지로 내용 스캔/제거가 안 됨
    check("⚠ 심각: 첨부파일 속 주민등록번호가 '마스킹 완료' 파일에 그대로 남아있음",
          has_embed and "900101-1234568" in content, content)


def test_hidden_widget_triggers_genuine_self_check_failure():
    print("test_hidden_widget_triggers_genuine_self_check_failure")
    tmp = _new_tmp()
    src = tmp / "widget.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "지출결의서 신청인: 김테스트", fontsize=11, fontname="korea")
    widget = fitz.Widget()
    widget.field_name = "연락처"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(72, 150, 300, 170)
    widget.field_value = "010-5555-6666"
    page.add_widget(widget)
    doc.save(src)
    doc.close()

    out = tmp / "widget_masked.pdf"
    findings, result = _mask(src, out)
    check("폼필드 값은 본문 텍스트 추출에 포함되어 탐지는 됨(주석/첨부와 다름)",
          any(f.value == "010-5555-6666" for f in findings), str(findings))
    # ⚠ 참고할 만한 사실이지 버그는 아님: PyMuPDF의 add_redact_annot/apply_redactions가
    # 폼필드 위젯의 외형(appearance stream)까지는 지우지 못해 실제로 재검증에 걸린다.
    # 이건 "안전망이 의도대로 작동한 것"(설계서 6.5.1: 확실하지 않으면 저장 안 함) --
    # 다만 현재 masker.py엔 이걸 우회할 대체 경로(6.5.1이 언급한 '페이지 이미지화' 등)가
    # 아직 구현돼 있지 않아, 폼필드에 PII가 있는 실제 문서는 이 도구로 영구히 처리 불가하다.
    check("자체 재검증이 실패로 보고됨 (안전망 정상 작동, 저장 안 함)", not result.success)
    check("output_path는 None (아무 파일도 안 만들어짐)", result.output_path is None)
    check("leftover에 폼필드 값이 정확히 찍힘", result.leftover == ["010-5555-6666"], str(result.leftover))
    check("hidden_content_warning도 True (이중으로 경고)", result.hidden_content_warning)


def main():
    tests = [
        test_basic_roundtrip_byte_level_removal,
        test_metadata_scrubbed,
        test_rotated_text_pii_removed_but_replacement_rendering_is_broken,
        test_hidden_annotation_content_survives_unscrubbed,
        test_hidden_embedded_file_survives_unscrubbed,
        test_hidden_widget_triggers_genuine_self_check_failure,
    ]
    for t in tests:
        t()

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 masker 테스트 통과 ({len(tests)}개 시나리오) -- ⚠ 표시된 항목은 '현재 동작을 고정'한 것이지 바람직한 동작이 아님")
    sys.exit(0)


if __name__ == "__main__":
    main()
