"""
6.3.4 진행바 세부 보강 기능 스트레스 테스트: 경고 페이지 색 강조 + "안 본
페이지만 보기" 필터.

- masker.rotated_text_pages()/hidden_content_pages()가 실제로 페이지 단위로
  정확한 번호를 돌려주는지(문서 전체에 걸린 boolean이 아니라).
- review_ui.ReviewWindow가 그 페이지 번호를 받아 진행바 버튼에 경고 표시를
  하는지, 검토완료(초록)와 독립적으로 계속 남아있는지.
- 필터를 켜면 이미 검토완료된 페이지의 항목 행이 숨겨지고, 페이지가 새로
  검토완료될 때마다 그 페이지도 자동으로 숨겨지는지.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from PySide6.QtWidgets import QApplication

import dictionary_learning
import template_fingerprint
from detector import Finding
from masker import hidden_content_pages, rotated_text_pages
from review_ui import ReviewWindow

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


# ---------------------------------------------------------------------------
def test_rotated_text_pages_is_per_page_not_whole_doc():
    print("test_rotated_text_pages_is_per_page_not_whole_doc")
    tmp = _new_tmp("rot_pages_")
    src = tmp / "doc.pdf"
    doc = fitz.open()
    doc.new_page()  # 0페이지: 평범한 텍스트만
    doc[0].insert_text((72, 72), "일반 텍스트", fontsize=11, fontname="korea")
    doc.new_page()  # 1페이지: 회전된 텍스트
    doc[1].insert_textbox(fitz.Rect(72, 150, 100, 400), "주민등록번호: 900101-1234568",
                           fontsize=11, fontname="korea", rotate=90)
    doc.new_page()  # 2페이지: 다시 평범한 텍스트
    doc[2].insert_text((72, 72), "일반 텍스트2", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    pages = rotated_text_pages(doc2)
    doc2.close()
    check("회전된 텍스트가 있는 1페이지만 정확히 포함됨", pages == {1}, str(pages))


def test_hidden_content_pages_annotation_is_per_page():
    print("test_hidden_content_pages_annotation_is_per_page")
    tmp = _new_tmp("hidden_pages_annot_")
    src = tmp / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc[1].add_text_annot((72, 150), "메모: 010-9999-8888")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    pages = hidden_content_pages(doc2)
    doc2.close()
    check("주석이 있는 1페이지만 포함됨(0페이지는 제외)", pages == {1}, str(pages))


def test_hidden_content_pages_embedded_file_marks_all_pages():
    print("test_hidden_content_pages_embedded_file_marks_all_pages")
    tmp = _new_tmp("hidden_pages_embed_")
    src = tmp / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.new_page()
    doc.embfile_add("memo.txt", "주민번호: 900101-1234568".encode("utf-8"), filename="memo.txt")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    pages = hidden_content_pages(doc2)
    doc2.close()
    check("첨부파일은 특정 페이지로 단정할 수 없어 모든 페이지에 경고가 붙음",
          pages == {0, 1, 2}, str(pages))


def test_no_warnings_returns_empty_set():
    print("test_no_warnings_returns_empty_set")
    tmp = _new_tmp("no_warn_")
    src = tmp / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "평범한 문서입니다", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    doc2 = fitz.open(src)
    check("회전 텍스트 없음", rotated_text_pages(doc2) == set())
    check("숨겨진 콘텐츠 없음", hidden_content_pages(doc2) == set())
    doc2.close()


# ---------------------------------------------------------------------------
def test_warning_page_highlighted_independent_of_reviewed(app):
    print("test_warning_page_highlighted_independent_of_reviewed")
    findings = [Finding("이름", "홍길동", 0, 3), Finding("전화번호", "010-1111-2222", 4, 17)]
    window = ReviewWindow("doc.pdf", findings, total_pages=2, warning_pages={0})
    window.show()

    btn0 = window.page_buttons[0]
    btn1 = window.page_buttons[1]
    check("경고 페이지 버튼에 ⚠ 표시가 붙음", "⚠" in btn0.text(), btn0.text())
    check("경고 없는 페이지 버튼엔 ⚠가 없음", "⚠" not in btn1.text(), btn1.text())
    check("경고 페이지 버튼에 주황 테두리 스타일이 적용됨", "e69500" in btn0.styleSheet(), btn0.styleSheet())
    check("경고 없는 페이지엔 테두리 스타일이 없음", "e69500" not in btn1.styleSheet(), btn1.styleSheet())

    # 검토완료로 바뀐 뒤에도(초록 배경) 경고(주황 테두리)는 계속 남아있어야 함
    window.page_reviewed[0] = True
    window._refresh_page_buttons()
    check("검토완료 후 배경은 초록으로 바뀜", "b7e3b7" in btn0.styleSheet(), btn0.styleSheet())
    check("검토완료 후에도 경고 테두리는 그대로 남음", "e69500" in btn0.styleSheet(), btn0.styleSheet())
    window.close()


def test_unseen_filter_hides_reviewed_rows_and_updates_dynamically(app):
    print("test_unseen_filter_hides_reviewed_rows_and_updates_dynamically")
    f_page0 = Finding("이름", "홍길동", 0, 3)
    f_page1 = Finding("전화번호", "010-1111-2222", 100, 113)
    window = ReviewWindow("doc.pdf", [f_page0, f_page1], total_pages=2)
    window.finding_page[id(f_page0)] = 0
    window.finding_page[id(f_page1)] = 1
    window.show()
    # 이 작은 테스트 창에서는 둘 다 처음부터 뷰포트 안에 들어와 showEvent에서
    # _check_visible_rows()가 즉시 둘 다 검토완료로 표시해버림 -- 실사용에서는
    # 문서가 길어 한 화면에 다 안 들어오는 게 보통이라 문제가 안 되지만, 이
    # 테스트는 "필터가 page_reviewed 변화에 반응하는지"만 보고 싶으므로 깨끗한
    # 상태(둘 다 미검토)로 되돌려서 시작한다.
    window.page_reviewed = {0: False, 1: False}

    check("필터 끈 상태에선 두 항목 모두 보임",
          window.row_widgets[id(f_page0)].isVisible() and window.row_widgets[id(f_page1)].isVisible())

    window.unseen_only_toggle.setChecked(True)
    check("필터를 켜도 둘 다 아직 미검토라 계속 보임",
          window.row_widgets[id(f_page0)].isVisible() and window.row_widgets[id(f_page1)].isVisible())

    # 0페이지가 검토완료로 바뀌면(_refresh_page_buttons를 통해) 필터가 자동으로 그 행을 숨김
    window.page_reviewed[0] = True
    window._refresh_page_buttons()
    check("0페이지 검토완료 후 필터가 켜져 있으면 그 행이 숨겨짐",
          not window.row_widgets[id(f_page0)].isVisible())
    check("아직 미검토인 1페이지 항목은 계속 보임", window.row_widgets[id(f_page1)].isVisible())

    window.unseen_only_toggle.setChecked(False)
    check("필터를 끄면 검토완료된 항목도 다시 보임", window.row_widgets[id(f_page0)].isVisible())
    window.close()


def test_unseen_filter_toggle_is_not_a_qcheckbox(app):
    print("test_unseen_filter_toggle_is_not_a_qcheckbox")
    # end-to-end 테스트가 findChildren(QCheckBox)[0]으로 "첫 번째 항목 체크박스"를
    # 찾는데, 이 필터 토글이 QCheckBox이면 그 탐색에 잘못 걸려 엉뚱한 걸 클릭하게 됨
    # (실측으로 발견한 회귀) -- QPushButton(checkable)이어야 안전.
    from PySide6.QtWidgets import QCheckBox
    window = ReviewWindow("doc.pdf", [Finding("이름", "홍길동", 0, 3)], total_pages=1)
    checkbox_texts = [cb.text() for cb in window.findChildren(QCheckBox)]
    check("필터 토글 문구가 findChildren(QCheckBox) 결과에 섞여 나오지 않음",
          "안 본 페이지만 보기" not in checkbox_texts, str(checkbox_texts))
    window.close()


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_rotated_text_pages_is_per_page_not_whole_doc,
        test_hidden_content_pages_annotation_is_per_page,
        test_hidden_content_pages_embedded_file_marks_all_pages,
        test_no_warnings_returns_empty_set,
    ]
    for t in tests:
        t()

    ui_tests = [
        test_warning_page_highlighted_independent_of_reviewed,
        test_unseen_filter_hides_reviewed_rows_and_updates_dynamically,
        test_unseen_filter_toggle_is_not_a_qcheckbox,
    ]
    for t in ui_tests:
        t(app)

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 진행바 세부 보강 테스트 통과 ({len(tests) + len(ui_tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
