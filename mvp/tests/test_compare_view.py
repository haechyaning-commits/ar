"""
compare_view.py (6.3.2 원본-마스킹본 비교 뷰) 스트레스 테스트.

① 승인 전 미리보기(PreviewCompareDialog): 체크 상태에 따라 오른쪽 미리보기에만
검은 오버레이가 그려지고 실시간으로 갱신되는지, 왼쪽(원본)은 항상 그대로인지
실제 픽셀 색을 읽어 확인한다.
② 승인 후 비교(SideBySidePdfDialog/open_result_comparison): 저장된 두 파일을
예외 없이 열고 자동으로 닫히는지 확인한다.
검토 화면(review_ui.py) 통합: "미리보기 비교" 버튼이 실제 클릭으로 창을 띄우고,
체크박스 토글이 열려있는 미리보기를 갱신하는지 실제 Qt 이벤트로 확인한다.

디스플레이 없는 환경에서 실행하려면 Xvfb로 가상 디스플레이를 띄워야 한다:
    xvfb-run -a python3 mvp/tests/test_compare_view.py

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

import compare_view
import dictionary_learning
import template_fingerprint
from compare_view import PreviewCompareDialog
from detector import detect_all
from page_viewer import RENDER_SCALE
from pdf_extract import extract_text_and_spans, rects_for_range
from review_ui import ReviewWindow

# 6.2.1/6.2.2가 승인 클릭마다 실제 mvp/data/를 오염시키지 않도록 격리 (다른 테스트 파일과 동일한 조치)
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


def _is_black(color) -> bool:
    return color.red() < 10 and color.green() < 10 and color.blue() < 10


def click_checkbox(cb):
    QTest.mouseClick(cb, Qt.LeftButton, Qt.NoModifier, QPoint(10, cb.height() // 2))


def click_button(window, text_prefix: str):
    btn = next(b for b in window.findChildren(QPushButton) if b.text().startswith(text_prefix))
    QTest.mouseClick(btn, Qt.LeftButton)


# ---------------------------------------------------------------------------
def test_preview_overlay_only_on_checked_items(app):
    print("test_preview_overlay_only_on_checked_items")
    tmp = _new_tmp("compare_preview_")
    src = tmp / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "성명: 홍길동", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    full_text, spans = extract_text_and_spans(doc2)
    doc2.close()
    findings = detect_all(full_text)
    name_finding = next(f for f in findings if f.type == "이름")

    rp = rects_for_range(full_text, spans, name_finding.start, name_finding.end)[0]
    x0, y0, x1, y1 = rp.rect
    cx = int(((x0 + x1) / 2) * RENDER_SCALE)
    cy = int(((y0 + y1) / 2) * RENDER_SCALE)

    state = {"checked": True}
    dlg = PreviewCompareDialog("doc.pdf", str(src), full_text, spans, findings, lambda f: state["checked"])

    color_checked = dlg.right_image.pixmap().toImage().pixelColor(cx, cy)
    check("체크된 상태 -- 오른쪽(미리보기)에 검은 오버레이가 그려짐",
          _is_black(color_checked), str(color_checked.getRgb()))

    state["checked"] = False
    dlg.refresh()  # review_ui.py가 체크박스 토글마다 호출하는 것과 동일
    color_unchecked = dlg.right_image.pixmap().toImage().pixelColor(cx, cy)
    check("체크 해제 후 refresh() -- 오버레이가 사라짐(실시간 갱신 확인)",
          not _is_black(color_unchecked), str(color_unchecked.getRgb()))

    left_color = dlg.left_image.pixmap().toImage().pixelColor(cx, cy)
    check("왼쪽(원본)은 체크 상태와 무관하게 항상 오버레이 없음",
          not _is_black(left_color), str(left_color.getRgb()))

    dlg.close()


def test_side_by_side_opens_and_autocloses(app):
    print("test_side_by_side_opens_and_autocloses")
    tmp = _new_tmp("compare_sbs_")
    left = tmp / "left.pdf"
    right = tmp / "right.pdf"
    for path, text in [(left, "원본 문서"), (right, "마스킹된 문서")]:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11, fontname="korea")
        doc.save(path)
        doc.close()

    raised = False
    try:
        compare_view.open_result_comparison(str(left), str(right), _auto_close_after_ms=50)
    except Exception as exc:  # noqa: BLE001 -- 실측 확인용, 어떤 예외든 실패로 기록
        raised = True
        check("open_result_comparison 예외 없이 실행됨", False, f"{type(exc).__name__}: {exc}")
    if not raised:
        check("open_result_comparison 예외 없이 실행되고 자동으로 닫힘", True)


def test_review_window_preview_button_and_live_refresh(app):
    print("test_review_window_preview_button_and_live_refresh")
    tmp = _new_tmp("compare_reviewui_")
    src = tmp / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "성명: 홍길동", fontsize=11, fontname="korea")
    page.insert_text((72, 100), "전화번호: 010-1111-2222", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    full_text, spans = extract_text_and_spans(doc2)
    total_pages = doc2.page_count
    doc2.close()
    findings = detect_all(full_text)

    window = ReviewWindow("doc.pdf", findings, full_text, spans, total_pages, str(src))
    window.show()

    preview_buttons = [b for b in window.findChildren(QPushButton)
                        if b.text().startswith("원본-마스킹본 미리보기 비교")]
    check("input_path가 있으면 미리보기 비교 버튼이 표시됨", len(preview_buttons) == 1, str(preview_buttons))

    click_button(window, "원본-마스킹본 미리보기 비교")
    check("버튼 클릭으로 미리보기 창이 열림", window._preview_dialog is not None)

    # 체크박스를 토글하면 열려있는 미리보기가 예외 없이 갱신되는지 확인
    cb, _f = window.checkboxes[0]
    click_checkbox(cb)
    check("체크박스 토글 후에도 미리보기 창이 여전히 열려있음(크래시 없음)",
          window._preview_dialog is not None)

    window._preview_dialog.close()
    check("미리보기 창을 닫으면 참조가 정리됨(finished 시그널)", window._preview_dialog is None)

    window.close()


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_preview_overlay_only_on_checked_items,
        test_side_by_side_opens_and_autocloses,
        test_review_window_preview_button_and_live_refresh,
    ]
    for t in tests:
        t(app)

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 compare_view 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
