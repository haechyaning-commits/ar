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


# -- 아이디어 1: 확대/축소 + 폭 맞춤 ------------------------------------------
def test_zoom_in_out_changes_canvas_scale(app):
    print("test_zoom_in_out_changes_canvas_scale")
    window, _findings = _open_review_window(app)
    base_scale = window.canvas.scale
    base_width = window.canvas.pixmap().width()

    window._set_canvas_zoom(window.canvas_zoom * 2)
    check("확대하면 캔버스 배율이 커짐", window.canvas.scale > base_scale, str(window.canvas.scale))
    check("확대하면 이미지 폭도 커짐", window.canvas.pixmap().width() > base_width)
    check("배율 표시 라벨이 갱신됨", "%" in window.zoom_label.text(), window.zoom_label.text())

    window._set_canvas_zoom(0.01)  # 하한 미만 요청 -> CANVAS_ZOOM_MIN으로 클램프
    from review_ui import CANVAS_ZOOM_MIN
    check("축소는 CANVAS_ZOOM_MIN 아래로 안 내려감", window.canvas_zoom == CANVAS_ZOOM_MIN, str(window.canvas_zoom))
    window.close()


def test_fit_to_width_matches_viewport(app):
    print("test_fit_to_width_matches_viewport")
    window, _findings = _open_review_window(app)
    window.resize(900, 640)
    window._fit_canvas_to_width()
    viewport_width = window._canvas_scroll.viewport().width()
    check("폭 맞춤 후 이미지 폭이 뷰포트 폭 근처로 맞춰짐",
          abs(window.canvas.pixmap().width() - viewport_width) <= viewport_width * 0.15,
          f"canvas={window.canvas.pixmap().width()} viewport={viewport_width}")
    window.close()


# -- 아이디어 2: 사이드바 호버 시 캔버스 강조 ---------------------------------
def test_row_hover_highlights_canvas_box(app):
    print("test_row_hover_highlights_canvas_box")
    window, findings = _open_review_window(app)
    name_finding = next(f for f in findings if f.type == "이름")
    row = window.row_widgets[id(name_finding)]

    check("호버 전에는 강조 대상 없음", window.canvas.hover_target is None)
    window._on_row_hover(name_finding, True)
    check("호버하면 캔버스가 그 항목을 강조 대상으로 기록함", window.canvas.hover_target is name_finding)
    window._on_row_hover(name_finding, False)
    check("호버를 떼면 강조가 풀림", window.canvas.hover_target is None)
    window.close()


# -- 아이디어 3: 연속 스크롤 보기 --------------------------------------------
def test_continuous_mode_shows_every_page(app):
    print("test_continuous_mode_shows_every_page")
    tmp = _new_tmp()
    src = tmp / "여러페이지.pdf"
    doc = fitz.open()
    p0 = doc.new_page()
    p0.insert_text((72, 72), "성명: 김민준", fontsize=11, fontname="korea")
    p1 = doc.new_page()
    p1.insert_text((72, 72), "연락처: 010-1111-2222", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    full_text, spans = extract_text_and_spans(doc2)
    sizes = page_sizes(doc2)
    doc2.close()
    findings = detect_all(full_text)

    window = ReviewWindow("여러페이지.pdf", findings, full_text, spans, 2, str(src), page_sizes=sizes)
    window.show()
    check("초기(단일 페이지 모드)엔 이전/다음 버튼이 보임", window.prev_page_btn.isVisible())

    QTest.mouseClick(window.continuous_toggle, Qt.LeftButton)
    check("토글 클릭 후 연속 모드로 전환됨", window.continuous_mode)
    check("페이지 수만큼 캔버스가 생성됨", len(window._canvas_widgets_by_page) == 2,
          str(len(window._canvas_widgets_by_page)))
    check("연속 모드에선 이전/다음 버튼이 숨겨짐", not window.prev_page_btn.isVisible())

    QTest.mouseClick(window.continuous_toggle, Qt.LeftButton)
    check("다시 끄면 단일 페이지 모드로 복귀", not window.continuous_mode)
    check("이전/다음 버튼이 다시 보임", window.prev_page_btn.isVisible())
    window.close()


# -- 아이디어 4: Undo/Redo ---------------------------------------------------
def test_undo_redo_checkbox_toggle(app):
    print("test_undo_redo_checkbox_toggle")
    window, findings = _open_review_window(app)
    phone_finding = next(f for f in findings if f.type == "전화번호")
    cb = next(cb for cb, f in window.checkboxes if f is phone_finding)
    check("클릭 전 기본 체크 상태", cb.isChecked())

    QTest.mouseClick(cb, Qt.LeftButton, Qt.NoModifier, QPoint(10, cb.height() // 2))
    check("클릭 후 해제됨", not cb.isChecked())

    window._undo()
    check("Ctrl+Z로 되돌리면 다시 체크됨", cb.isChecked())

    window._redo()
    check("Ctrl+Shift+Z로 다시 실행하면 다시 해제됨", not cb.isChecked())

    window._undo()
    window._undo()
    check("더 되돌릴 게 없으면 안내만 표시하고 크래시 없음", "없습니다" in window.status_label.text())
    window.close()


def test_undo_redo_canvas_add(app):
    print("test_undo_redo_canvas_add")
    window, findings = _open_review_window(app)
    before_count = len(window.findings)
    page = window._canvas_doc[0]
    target_word = next(w for w in page.get_text("words") if w[4] == "최민아")
    px = int((target_word[0] + target_word[2]) / 2 * RENDER_SCALE)
    py = int((target_word[1] + target_word[3]) / 2 * RENDER_SCALE)
    QTest.mouseClick(window.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(px, py))
    check("클릭으로 항목이 추가됨", len(window.findings) == before_count + 1)

    window._undo()
    check("Undo하면 추가된 항목이 사라짐", len(window.findings) == before_count)
    check("체크박스 목록에서도 사라짐", all(f.value != "최민아" for _cb, f in window.checkboxes))

    window._redo()
    check("Redo하면 다시 추가됨", len(window.findings) == before_count + 1)
    check("체크박스 목록에도 다시 나타남", any(f.value == "최민아" for _cb, f in window.checkboxes))
    window.close()


def test_undo_redo_set_all(app):
    print("test_undo_redo_set_all")
    window, findings = _open_review_window(app)
    check("초기엔 전부 체크됨", all(cb.isChecked() for cb, _f in window.checkboxes))

    window._set_all(False)
    check("전체 해제 후 전부 해제됨", all(not cb.isChecked() for cb, _f in window.checkboxes))

    window._undo()
    check("Undo 한 번으로 전체가 다시 체크됨(묶음 undo)", all(cb.isChecked() for cb, _f in window.checkboxes))
    window.close()


# -- 아이디어 5: 캔버스 우클릭 삭제 ------------------------------------------
def test_canvas_delete_removes_finding_and_is_undoable(app):
    print("test_canvas_delete_removes_finding_and_is_undoable")
    window, findings = _open_review_window(app)
    name_finding = next(f for f in findings if f.type == "이름")
    before_count = len(window.findings)

    # QMenu 팝업 자체(네이티브 Qt UI)는 건드리지 않고, 우클릭 메뉴에서 "삭제"를
    # 선택했을 때 ReviewWindow가 실제로 하는 일(_on_canvas_delete)을 검증
    window._on_canvas_delete(name_finding)
    check("삭제 후 findings에서 빠짐", len(window.findings) == before_count - 1)
    check("체크박스 목록에서도 빠짐", all(f is not name_finding for _cb, f in window.checkboxes))

    window._undo()
    check("Undo하면 다시 목록에 나타남", any(f is name_finding for _cb, f in window.checkboxes))
    check("findings 개수도 원복됨", len(window.findings) == before_count)
    window.close()


# -- 아이디어 6: 경고 페이지 강제 확인 ----------------------------------------
def test_approve_blocked_until_warning_page_seen_on_canvas(app):
    print("test_approve_blocked_until_warning_page_seen_on_canvas")
    tmp = _new_tmp()
    src = tmp / "경고문서.pdf"
    doc = fitz.open()
    p0 = doc.new_page()
    p0.insert_text((72, 72), "성명: 김민준", fontsize=11, fontname="korea")
    p1 = doc.new_page()
    p1.insert_text((72, 72), "연락처: 010-1111-2222", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    full_text, spans = extract_text_and_spans(doc2)
    sizes = page_sizes(doc2)
    doc2.close()
    findings = detect_all(full_text)

    # 경고는 1페이지(0-based index 1)에 있다고 가정 -- 캔버스는 시작 시 0페이지만 봄
    window = ReviewWindow("경고문서.pdf", findings, full_text, spans, 2, str(src),
                           page_sizes=sizes, warning_pages={1})
    window.show()
    # 스크롤(목록) 기준으로는 두 페이지 다 이미 확인된 것으로 시뮬레이션 -- 그래도
    # 경고 페이지(1)는 캔버스로 실제로 열어본 적이 없어야 승인이 막혀야 함
    window.page_reviewed[0] = True
    window.page_reviewed[1] = True

    window._on_approve()
    check("경고 페이지를 캔버스로 안 봤으면 승인이 차단됨", window.approved_result is None)
    check("안내 메시지에 경고 페이지 재확인 요청이 담김", "실제 화면으로" in window.status_label.text(),
          window.status_label.text())

    window._show_canvas_page(1)  # 실제로 캔버스에서 열어봄
    window._on_approve()
    check("캔버스로 열어본 뒤에는 승인됨", window.approved_result is True)
    window.close()


# -- 아이디어 7: 놓친 후보 힌트 ------------------------------------------------
def test_candidate_hint_click_converts_to_real_finding(app):
    print("test_candidate_hint_click_converts_to_real_finding")
    tmp = _new_tmp()
    src = tmp / "힌트문서.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "성명: 김민준", fontsize=11, fontname="korea")
    # 문맥 라벨("계좌"/"입금"/"예금주") 없이 계좌번호 형식의 숫자만 있음
    # -> detect_account는 못 잡지만 candidate_hints는 힌트로 잡아야 함
    page.insert_text((72, 100), "메모: 123456-04-789012", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    full_text, spans = extract_text_and_spans(doc2)
    sizes = page_sizes(doc2)
    doc2.close()
    findings = detect_all(full_text)
    check("사전 준비: 계좌번호 힌트는 자동탐지되지 않음",
          all(f.type != "계좌번호" for f in findings), str(findings))

    window = ReviewWindow("힌트문서.pdf", findings, full_text, spans, 1, str(src), page_sizes=sizes)
    window.show()
    check("힌트 목록에 그 계좌번호 형식 값이 들어있음",
          any(h.value == "123456-04-789012" for h in window.candidate_hints_list),
          str(window.candidate_hints_list))
    check("캔버스에 힌트 박스가 그려짐", len(window.canvas.hints) >= 1)

    hint = next(h for h in window.candidate_hints_list if h.value == "123456-04-789012")
    before_count = len(window.findings)
    window._on_canvas_hint_clicked(hint)

    check("힌트를 클릭하면 정식 항목으로 추가됨", len(window.findings) == before_count + 1)
    new_f = window.findings[-1]
    check("추가된 값이 힌트와 동일", new_f.value == "123456-04-789012", new_f.value)
    check("추가된 항목은 정식 유형(계좌번호)으로 분류됨", new_f.type == "계좌번호", new_f.type)
    check("힌트 목록에서는 빠짐(재계산됨)",
          not any(h.value == "123456-04-789012" for h in window.candidate_hints_list),
          str(window.candidate_hints_list))
    window.close()


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_canvas_created_only_with_input_path,
        test_click_box_on_canvas_toggles_sidebar_checkbox,
        test_toggle_sidebar_checkbox_updates_canvas_color,
        test_click_missed_text_on_canvas_adds_new_finding,
        test_click_empty_area_with_no_text_shows_message_not_crash,
        test_zoom_in_out_changes_canvas_scale,
        test_fit_to_width_matches_viewport,
        test_row_hover_highlights_canvas_box,
        test_continuous_mode_shows_every_page,
        test_undo_redo_checkbox_toggle,
        test_undo_redo_canvas_add,
        test_undo_redo_set_all,
        test_canvas_delete_removes_finding_and_is_undoable,
        test_approve_blocked_until_warning_page_seen_on_canvas,
        test_candidate_hint_click_converts_to_real_finding,
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
