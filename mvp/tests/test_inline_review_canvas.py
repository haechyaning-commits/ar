"""
실사용자 피드백 대응(신규): 검토 화면에 내장된 실제 페이지 캔버스(review_ui._InlineReviewCanvas)가
실제로 동작하는지 검증. 기존엔 사이드바 체크리스트만 보여줘서 "실제 문서에 있는지/탐지가
덜 됐는지"를 확인할 수 없다는 지적이 있었음 -- 이 캔버스는 실제 페이지 이미지 위에 탐지
영역을 겹쳐 보여주고, 클릭으로 토글/추가까지 가능하게 함.

- 캔버스의 박스를 클릭하면 사이드바 체크박스가 토글됨(캔버스 -> 목록)
- 사이드바 체크박스를 토글하면 캔버스 박스 색(승인 여부)도 같이 바뀜(목록 -> 캔버스)
- 박스가 없는 자리(자동탐지가 놓친 텍스트)를 클릭하면 새 항목이 그 자리에 즉시 추가됨
- input_path가 없으면(원본을 다시 열 수 없으면) 캔버스 자체가 생성되지 않고 기존처럼 동작

xvfb-run -a python3 mvp/tests/test_inline_review_canvas.py 로 실행할 것.
실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import dictionary_learning
import template_fingerprint
from detector import detect_all
from page_viewer import RENDER_SCALE
from pdf_extract import extract_text_and_spans, page_sizes
from review_ui import ReviewWindow

# 실제 mvp/data/ 사전·지문 파일을 이 테스트가 건드리지 않도록 격리
dictionary_learning.DEFAULT_DATA_DIR = Path(tempfile.mkdtemp(prefix="canvas_test_dict_"))
template_fingerprint.DEFAULT_DATA_DIR = Path(tempfile.mkdtemp(prefix="canvas_test_tpl_"))

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def _new_tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="canvas_test_"))


def _build_doc(src: Path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "성명: 김민준", fontsize=11, fontname="korea")
    page.insert_text((72, 100), "연락처: 010-2345-6789", fontsize=11, fontname="korea")
    # 탐지 라벨 사전(NAME_LABELS)에 없는 라벨("동행")이라 자동탐지가 놓치는 이름
    page.insert_text((72, 128), "동행: 최민아", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()


def _open_review_window(app):
    tmp = _new_tmp()
    src = tmp / "출장복명서.pdf"
    _build_doc(src)

    doc = fitz.open(src)
    full_text, spans = extract_text_and_spans(doc)
    sizes = page_sizes(doc)
    doc.close()
    findings = detect_all(full_text)

    window = ReviewWindow("출장복명서.pdf", findings, full_text, spans, 1, str(src), page_sizes=sizes)
    window.show()
    return window, findings


def test_canvas_created_only_with_input_path(app):
    print("test_canvas_created_only_with_input_path")
    window, findings = _open_review_window(app)
    check("input_path가 있으면 캔버스가 생성됨", window.canvas is not None)
    check("탐지된 박스가 실제로 그려짐(findings 개수만큼)", len(window.canvas.boxes) == len(findings), str(len(window.canvas.boxes)))
    window.close()

    without_path = ReviewWindow("doc.pdf", [], total_pages=1)
    check("input_path가 없으면 캔버스가 생성되지 않음(원본을 다시 열 수 없으므로)", without_path.canvas is None)
    without_path.close()


def test_click_box_on_canvas_toggles_sidebar_checkbox(app):
    print("test_click_box_on_canvas_toggles_sidebar_checkbox")
    window, findings = _open_review_window(app)
    name_finding = next(f for f in findings if f.type == "이름")
    cb = next(cb for cb, f in window.checkboxes if f is name_finding)
    check("클릭 전 기본 체크 상태", cb.isChecked())

    rect = next(r for f, r, _a, _h in window._boxes_for_page(0) if f is name_finding)
    QTest.mouseClick(window.canvas, Qt.LeftButton, Qt.NoModifier, rect.center())

    check("캔버스에서 박스를 클릭하니 사이드바 체크박스가 해제됨", not cb.isChecked())
    approved_after = next(a for f, _r, a, _h in window._boxes_for_page(0) if f is name_finding)
    check("캔버스 박스 색도 approved=False로 반영됨(초록으로 바뀔 상태)", not approved_after)
    window.close()


def test_toggle_sidebar_checkbox_updates_canvas_color(app):
    print("test_toggle_sidebar_checkbox_updates_canvas_color")
    window, findings = _open_review_window(app)
    phone_finding = next(f for f in findings if f.type == "전화번호")
    cb = next(cb for cb, f in window.checkboxes if f is phone_finding)

    QTest.mouseClick(cb, Qt.LeftButton, Qt.NoModifier, QPoint(10, cb.height() // 2))
    check("사이드바에서 체크 해제한 직후 캔버스도 approved=False로 동기화됨",
          not next(a for f, _r, a, _h in window._boxes_for_page(0) if f is phone_finding))

    QTest.mouseClick(cb, Qt.LeftButton, Qt.NoModifier, QPoint(10, cb.height() // 2))
    check("다시 체크하면 캔버스도 approved=True로 동기화됨",
          next(a for f, _r, a, _h in window._boxes_for_page(0) if f is phone_finding))
    window.close()


def test_click_missed_text_on_canvas_adds_new_finding(app):
    print("test_click_missed_text_on_canvas_adds_new_finding")
    window, findings = _open_review_window(app)
    check("사전 준비: '최민아'는 라벨 사전에 없어 자동탐지에서 빠짐",
          all(f.value != "최민아" for f in findings), str(findings))
    before_count = len(window.findings)

    page = window._canvas_doc[0]
    target_word = next(w for w in page.get_text("words") if w[4] == "최민아")
    px = int((target_word[0] + target_word[2]) / 2 * RENDER_SCALE)
    py = int((target_word[1] + target_word[3]) / 2 * RENDER_SCALE)
    QTest.mouseClick(window.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(px, py))

    check("클릭 후 findings가 1건 늘어남", len(window.findings) == before_count + 1,
          f"{before_count} -> {len(window.findings)}")
    new_f = window.findings[-1]
    check("새 항목 값이 '최민아'", new_f.value == "최민아", new_f.value)
    check("새 항목이 사이드바 체크박스로도 즉시 나타남", any(f is new_f for _cb, f in window.checkboxes))
    check("새 항목이 기본으로 체크(마스킹 예정)됨", new_f.approved)
    check("새 항목이 캔버스에도 바로 박스로 반영됨",
          any(f is new_f for f, _r, _a, _h in window._boxes_for_page(0)))
    window.close()


def test_click_empty_area_with_no_text_shows_message_not_crash(app):
    print("test_click_empty_area_with_no_text_shows_message_not_crash")
    window, findings = _open_review_window(app)
    before_count = len(window.findings)
    # 페이지 맨 아래 빈 여백(텍스트 없는 영역) 클릭
    QTest.mouseClick(window.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(20, 500))
    check("텍스트 없는 영역 클릭 시 findings가 늘지 않음(크래시 없음)", len(window.findings) == before_count)
    check("안내 메시지가 표시됨", "찾지 못했습니다" in window.status_label.text(), window.status_label.text())
    window.close()


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_canvas_created_only_with_input_path,
        test_click_box_on_canvas_toggles_sidebar_checkbox,
        test_toggle_sidebar_checkbox_updates_canvas_color,
        test_click_missed_text_on_canvas_adds_new_finding,
        test_click_empty_area_with_no_text_shows_message_not_crash,
    ]
    for t in tests:
        t(app)

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 실제 페이지 캔버스 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
