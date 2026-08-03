"""
masker.py 스트레스 테스트 -- 리댁션/자체재검증/숨김콘텐츠/메타데이터/회전텍스트.

회전 텍스트 중복삽입, 숨김 콘텐츠(주석/첨부/폼필드) 미스크러빙 버그를 이 테스트로
실측 발견해 masker.py에서 수정했고, 아래 테스트는 수정된(올바른) 동작을 검증한다.

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
from pdf_extract import extract_text_and_spans

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
    # masker.py는 pdf_extract.extract_text_and_spans()가 만든 오프셋 기준으로
    # Finding.start/end를 실제 좌표로 되짚으므로, 탐지 쪽도 반드시 같은 함수를
    # 써야 offset이 어긋나지 않는다 (main.py와 동일한 방식, v15 위치 기반 마스킹).
    full_text, _spans = extract_text_and_spans(doc)
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


def test_rotated_text_masked_correctly_no_duplicate():
    print("test_rotated_text_masked_correctly_no_duplicate")
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
    check("회전 텍스트 경고 플래그가 True (설계서 6.5.1-4 완화책, 육안 재확인 권고는 유지)",
          result.rotated_text_warning)
    check("이번 케이스는 sort=True 덕분에 탐지 자체는 됨 (RRN 1건)",
          len(findings) == 1 and findings[0].type == "주민등록번호", str(findings))
    check("자체 재검증 통과(원문 숫자는 안 남음)", result.success, str(result.leftover))

    masked_doc = fitz.open(out)
    lines_info = [(s["text"], line.get("dir"))
                  for b in masked_doc[0].get_text("dict")["blocks"]
                  for line in b.get("lines", []) for s in line["spans"]]
    masked_doc.close()
    mask_lines = [(t, d) for t, d in lines_info if "*" in t]
    # 회전 방향(dir)을 반영하도록 고친 뒤: 대체 텍스트가 정확히 한 곳에만, 원본과 같은
    # 세로 방향(dir=(0,-1))으로 삽입되고 중복이 없어야 한다 (이전엔 2곳에 가로로 중복 삽입됐음).
    check("마스킹 텍스트가 정확히 한 곳에만 삽입됨(중복 없음)",
          len(mask_lines) == 1, f"{len(mask_lines)}개: {mask_lines}")
    if mask_lines:
        text, direction = mask_lines[0]
        check("마스킹된 값이 완전마스킹 형태로 들어감", text == "주민등록번호: ******-*******", text)
        check("대체 텍스트가 원본과 같은 회전 방향(세로, dir=(0,-1))으로 삽입됨",
              direction is not None and abs(direction[0]) < 0.01 and direction[1] < 0, str(direction))


def test_hidden_annotation_content_gets_scrubbed():
    print("test_hidden_annotation_content_gets_scrubbed")
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
    check("본문 탐지 결과에는 주석 속 전화번호가 없음(주석은 본문과 별도로 독립 스캔됨)",
          all(f.value != "010-9999-8888" for f in findings), str(findings))
    check("자체 재검증 통과(스크러빙 후 hidden_content_leftover까지 재확인)", result.success, str(result.leftover))
    check("hidden_content_warning 플래그는 True (숨김 콘텐츠가 있었다는 사실은 계속 알림)",
          result.hidden_content_warning)

    masked_doc = fitz.open(out)
    annots = list(masked_doc[0].annots() or [])
    contents = [a.info.get("content", "") for a in annots]
    masked_doc.close()
    # 수정 후: 검토자가 승인한 적 없는 값이라도 주석 안의 PII는 독립적으로 탐지해
    # 그 자리에서 마스킹한다 (본문과 동일한 마스킹 형식: 전화번호 -> 부분마스킹).
    check("주석의 원본 전화번호는 사라짐", all("010-9999-8888" not in c for c in contents), str(contents))
    check("주석 내용이 마스킹된 형태로 대체됨(라벨 텍스트는 보존)",
          contents == ["메모: 실제 연락처는 010-****-8888 입니다"], str(contents))


def test_hidden_embedded_file_gets_scrubbed():
    print("test_hidden_embedded_file_gets_scrubbed")
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
    check("자체 재검증 통과(스크러빙 후 hidden_content_leftover까지 재확인)", result.success, str(result.leftover))

    masked_doc = fitz.open(out)
    has_embed = masked_doc.embfile_count() > 0
    content = masked_doc.embfile_get(0).decode("utf-8") if has_embed else ""
    masked_doc.close()
    check("첨부파일이 삭제되지 않고 그대로 유지되되 내용만 마스킹됨",
          has_embed and content == "주민번호: ******-*******", content)


def test_hidden_widget_gets_scrubbed_and_passes():
    print("test_hidden_widget_gets_scrubbed_and_passes")
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
    # 이전엔 add_redact_annot/apply_redactions가 위젯 외형(appearance stream)을 못 지워
    # 재검증에 걸려 저장 자체가 영구히 막혔다. widget.field_value를 직접 마스킹된 값으로
    # 바꾸고 widget.update()로 외형을 재생성하는 방식으로 실제로 지워지도록 수정.
    check("자체 재검증 통과(더 이상 영구 차단되지 않음)", result.success, str(result.leftover))
    check("output_path가 정상 생성됨", result.output_path is not None)
    check("hidden_content_warning은 True (폼필드가 있었다는 사실은 계속 알림)", result.hidden_content_warning)

    masked_doc = fitz.open(out)
    field_value = masked_doc[0].first_widget.field_value
    masked_doc.close()
    check("폼필드 값이 마스킹된 형태로 저장됨", field_value == "010-****-6666", field_value)


def test_freetext_annotation_orphan_bytes_purged():
    print("test_freetext_annotation_orphan_bytes_purged")
    tmp = _new_tmp()
    src = tmp / "freetext.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "지출결의서 신청인: 김테스트", fontsize=11, fontname="korea")
    # FreeText 주석은 Text(스티키노트)와 달리 본문 텍스트 추출에도 내용이 잡히고,
    # 리댁션이 겹치면 PDF 스펙상 주석 자체가 삭제된다 (실측 확인).
    page.add_freetext_annot(fitz.Rect(72, 150, 300, 200),
                             "연락처 010-1111-2222, 이메일 abc@test.com")
    doc.save(src)
    doc.close()

    out = tmp / "freetext_masked.pdf"
    findings, result = _mask(src, out)
    check("자체 재검증 통과", result.success, str(result.leftover))

    masked_doc = fitz.open(out)
    check("리댁션이 겹치면서 FreeText 주석 자체가 삭제됨(부작용, PII 유출은 아님)",
          len(list(masked_doc[0].annots() or [])) == 0)
    masked_doc.close()

    # ⚠ 실측으로 발견한 핵심 문제: 주석이 '삭제'돼도 doc.save()가 가비지 컬렉션 없이
    # 저장하면, 참조가 끊긴 주석의 원본(마스킹 전) 어피어런스 스트림 객체가 파일
    # 바이트에는 그대로 남아있었다 -- get_text() 기반 self_check는 이걸 못 잡음
    # (더 이상 어디서도 '참조'되지 않는 죽은 객체라서). doc.save(..., garbage=4)로
    # 고치고, raw_byte_leftover()로 저장된 파일을 직접 재검증하도록 안전망도 추가함.
    raw = out.read_bytes()
    check("⚠ 원본 전화번호가 저장된 파일 바이트 어디에도 없음(고아 객체 포함)",
          b"010-1111-2222" not in raw)
    check("⚠ 원본 이메일이 저장된 파일 바이트 어디에도 없음(고아 객체 포함)",
          b"abc@test.com" not in raw)


def main():
    tests = [
        test_basic_roundtrip_byte_level_removal,
        test_metadata_scrubbed,
        test_rotated_text_masked_correctly_no_duplicate,
        test_hidden_annotation_content_gets_scrubbed,
        test_hidden_embedded_file_gets_scrubbed,
        test_hidden_widget_gets_scrubbed_and_passes,
        test_freetext_annotation_orphan_bytes_purged,
    ]
    for t in tests:
        t()

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 masker 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
