"""
6.3.6 실사용자 편의 기능 스트레스 테스트.

- 항목 클릭 시 자동 확대: 각 항목 행의 "🔍 확대" 버튼을 누르면 그 항목 주변만
  고배율로 다시 렌더링한 팝업(_ZoomDialog)이 뜨는지 확인.
- "다음 미확정 항목" 순회: 확신도가 "높음"이 아닌 항목만 버튼/단축키(Ctrl+J)로
  순서대로 포커스 이동하는지, 확신도 "높음" 항목은 건너뛰는지 확인.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

import dictionary_learning
import template_fingerprint
from detector import Finding, detect_all
from pdf_extract import extract_text_and_spans
from review_ui import ReviewWindow, _ZoomDialog

dictionary_learning.DEFAULT_DATA_DIR = Path(tempfile.mkdtemp(prefix="dict_learning_test_"))
template_fingerprint.DEFAULT_DATA_DIR = Path(tempfile.mkdtemp(prefix="template_fp_test_"))

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def _new_tmp(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def click_button(window, text_prefix: str):
    btn = next(b for b in window.findChildren(QPushButton) if b.text().startswith(text_prefix))
    QTest.mouseClick(btn, Qt.LeftButton)


def _build_sample_pdf(tmp: Path) -> Path:
    src = tmp / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "성명: 홍길동", fontsize=11, fontname="korea")
    page.insert_text((72, 100), "전화번호: 010-1111-2222", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()
    return src


# ---------------------------------------------------------------------------
def test_zoom_button_present_only_with_input_path(app):
    print("test_zoom_button_present_only_with_input_path")
    tmp = _new_tmp("conv_zoom_gate_")
    src = _build_sample_pdf(tmp)
    doc = fitz.open(src)
    full_text, spans = extract_text_and_spans(doc)
    total_pages = doc.page_count
    doc.close()
    findings = detect_all(full_text)

    with_path = ReviewWindow("doc.pdf", findings, full_text, spans, total_pages, str(src))
    zoom_buttons = [b for b in with_path.findChildren(QPushButton) if b.text().startswith("🔍")]
    check("input_path가 있으면 항목마다 확대 버튼이 있음",
          len(zoom_buttons) == len(findings), f"{len(zoom_buttons)} vs {len(findings)}")
    with_path.close()

    without_path = ReviewWindow("doc.pdf", findings, full_text, spans, total_pages, "")
    zoom_buttons_none = [b for b in without_path.findChildren(QPushButton) if b.text().startswith("🔍")]
    check("input_path가 없으면 확대 버튼이 없음(원본을 다시 열 수 없으므로)",
          len(zoom_buttons_none) == 0, str(zoom_buttons_none))
    without_path.close()


def test_zoom_button_opens_zoomed_view(app):
    print("test_zoom_button_opens_zoomed_view")
    tmp = _new_tmp("conv_zoom_open_")
    src = _build_sample_pdf(tmp)
    doc = fitz.open(src)
    full_text, spans = extract_text_and_spans(doc)
    total_pages = doc.page_count
    doc.close()
    findings = detect_all(full_text)
    name_finding = next(f for f in findings if f.type == "이름")

    window = ReviewWindow("doc.pdf", findings, full_text, spans, total_pages, str(src))
    window.show()

    dlg = _ZoomDialog(name_finding, str(src), full_text, spans)
    check("확대 창 제목에 항목 정보가 들어감",
          "홍길동" in dlg.windowTitle(), dlg.windowTitle())
    from PySide6.QtWidgets import QLabel
    image_labels = [lbl for lbl in dlg.findChildren(QLabel) if lbl.pixmap() is not None and not lbl.pixmap().isNull()]
    check("확대된 이미지가 실제로 렌더링됨", len(image_labels) == 1, str(len(image_labels)))
    if image_labels:
        pix = image_labels[0].pixmap()
        check("확대 이미지가 화면 표시(RENDER_SCALE=1.5)보다 훨씬 큰 배율로 렌더링됨(ZOOM_FACTOR=4.0)",
              pix.width() > 100 and pix.height() > 20, f"{pix.width()}x{pix.height()}")
    dlg.close()
    window.close()


def test_zoom_view_missing_position_shows_message(app):
    print("test_zoom_view_missing_position_shows_message")
    # spans가 비어있으면(=위치를 못 찾음) 크래시 없이 안내 메시지만 보여줘야 함
    f = Finding("이름", "없는값", 0, 3)
    dlg = _ZoomDialog(f, "", "없는값", [])
    from PySide6.QtWidgets import QLabel
    texts = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    check("위치를 못 찾으면 안내 메시지를 보여줌(크래시 없음)",
          any("찾을 수 없습니다" in t for t in texts), str(texts))
    dlg.close()


def test_next_low_confidence_skips_high_confidence(app):
    print("test_next_low_confidence_skips_high_confidence")
    # "전화번호"는 정규식 기반이라 확신도 "높음" -- 순회 대상에서 제외돼야 함
    # "이름"은 라벨 기반이라 확신도 "중간" -- 순회 대상
    text = "성명: 홍길동\n전화번호: 010-1111-2222\n"
    findings = detect_all(text)
    check("테스트 데이터에 확신도 '높음'(전화번호)과 '중간'(이름)이 섞여 있음",
          {f.confidence for f in findings} == {"높음", "중간"}, str([(f.type, f.confidence) for f in findings]))

    window = ReviewWindow("doc.pdf", findings)
    window.show()

    click_button(window, "다음 미확정 항목")
    # 헤드리스(offscreen)에서는 이 창이 OS 차원의 "활성 창"이 아닐 수 있어
    # QApplication.focusWidget()(앱 전역)은 None을 돌려줄 수 있음 -- 이 창
    # 내부의 포커스 체인을 직접 보는 window.focusWidget()을 써야 안정적임.
    focused = window.focusWidget()
    focused_finding = next((f for cb, f in window.checkboxes if cb is focused), None)
    check("첫 클릭 -- 확신도 '높음'이 아닌 항목(이름)으로 포커스 이동",
          focused_finding is not None and focused_finding.confidence != "높음",
          str(focused_finding))

    click_button(window, "다음 미확정 항목")
    focused2 = window.focusWidget()
    focused_finding2 = next((f for cb, f in window.checkboxes if cb is focused2), None)
    check("후보가 1개뿐이면 다시 눌러도 같은 항목에 머무름(전화번호로 안 넘어감)",
          focused_finding2 is focused_finding, str(focused_finding2))

    window.close()


def test_next_low_confidence_no_candidates_shows_message(app):
    print("test_next_low_confidence_no_candidates_shows_message")
    # 정규식으로만 잡히는 항목만 있으면(전부 확신도 '높음') 순회 대상이 없음
    findings = detect_all("전화번호: 010-1111-2222\n")
    check("테스트 데이터가 전부 확신도 '높음'", all(f.confidence == "높음" for f in findings), str(findings))

    window = ReviewWindow("doc.pdf", findings)
    window.show()
    click_button(window, "다음 미확정 항목")
    check("순회 대상이 없으면 안내 메시지를 보여줌(크래시 없음)",
          "없습니다" in window.status_label.text(), window.status_label.text())
    window.close()


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_zoom_button_present_only_with_input_path,
        test_zoom_button_opens_zoomed_view,
        test_zoom_view_missing_position_shows_message,
        test_next_low_confidence_skips_high_confidence,
        test_next_low_confidence_no_candidates_shows_message,
    ]
    for t in tests:
        t(app)

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 실사용자 편의 기능 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
