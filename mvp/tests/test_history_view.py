"""
처리 이력 조회 UI(history_view.py, 실사용자 아이디어 8) 테스트.

- 실제 process_atomic()으로 몇 건을 처리해서 진짜 audit_log.csv/원본_보관/
  마스킹완료를 만든 뒤, HistoryDialog가 그 내용을 정확히 읽어와 보여주는지 확인
- 파일명 충돌로 원본_보관/에 "(1)" 등이 붙은 경우도 find_original이 최선노력으로
  찾아내는지 확인
- "선택 건 원본-결과물 비교" 클릭이 올바른 경로로 compare_view를 호출하는지 확인
  (SideBySidePdfDialog 자체의 모덜 동작은 compare_view 쪽 테스트가 이미 커버하므로,
  여기서는 호출 자체와 경로가 맞는지만 monkeypatch로 검증)
- main.py --history 진입점이 예외 없이 동작하는지 확인

xvfb-run -a python3 mvp/tests/test_history_view.py 로 실행할 것.
실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import compare_view
import history_view
import main as main_module
from detector import detect_all
from pdf_extract import extract_text_and_spans
from pipeline import process_atomic

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def _new_tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="history_test_"))


def _make_dummy_pdf(path: Path, name_value: str = "김테스트"):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), f"신청인: {name_value} (010-1234-5678)", fontsize=11, fontname="korea")
    doc.save(path)
    doc.close()


def _detect(path: Path):
    doc = fitz.open(path)
    full_text, _spans = extract_text_and_spans(doc)
    doc.close()
    return detect_all(full_text)


def test_history_dialog_lists_processed_files(app):
    print("test_history_dialog_lists_processed_files")
    ws = _new_tmp()
    src_dir = _new_tmp()

    results = []
    for i in range(3):
        src = src_dir / f"지출결의서_{i}.pdf"
        _make_dummy_pdf(src, f"김테스트{i}")
        r = process_atomic(str(src), _detect(src), str(ws), processor="감사팀", review_seconds=5.0 + i)
        check(f"{i+1}번째 더미 처리 성공", r.success)
        results.append(r)

    dlg = history_view.HistoryDialog(ws)
    check("이력에 3건 모두 보임", dlg.table.rowCount() == 3, str(dlg.table.rowCount()))
    check("최신 처리가 맨 위(역순)로 정렬됨",
          dlg.table.item(0, history_view.COLUMNS.index("output_filename")).text()
          == Path(results[-1].output_path).name)
    check("처리자 컬럼에 실값이 들어감",
          dlg.table.item(0, history_view.COLUMNS.index("processor")).text() == "감사팀")
    check("검토 소요(초) 컬럼도 표시됨",
          dlg.table.item(0, history_view.COLUMNS.index("review_seconds")).text() == "7.0")
    check("개인정보 실값(김테스트0 등)은 이력 테이블 어디에도 없음",
          not any("김테스트" in dlg.table.item(r, c).text()
                  for r in range(dlg.table.rowCount()) for c in range(dlg.table.columnCount())))
    dlg.close()


def test_history_dialog_empty_when_no_log(app):
    print("test_history_dialog_empty_when_no_log")
    ws = _new_tmp()
    dlg = history_view.HistoryDialog(ws)
    check("로그가 없으면 0건으로 표시됨(크래시 없음)", dlg.table.rowCount() == 0)
    dlg.close()


def test_find_original_handles_filename_collision(app):
    print("test_find_original_handles_filename_collision")
    ws = _new_tmp()
    src_dir1 = _new_tmp()
    src_dir2 = _new_tmp()

    src1 = src_dir1 / "지출결의서.pdf"
    _make_dummy_pdf(src1, "김테스트")
    r1 = process_atomic(str(src1), _detect(src1), str(ws))
    check("1차 처리 성공", r1.success)

    src2 = src_dir2 / "지출결의서.pdf"  # 같은 파일명 -> 원본_보관/에서 충돌
    _make_dummy_pdf(src2, "박테스트")
    r2 = process_atomic(str(src2), _detect(src2), str(ws))
    check("2차 처리 성공(파일명 겹쳐도)", r2.success)
    check("두 원본이 서로 다른 이름으로 보존됨(충돌 회피 확인)",
          Path(r1.original_moved_to).name != Path(r2.original_moved_to).name)

    originals_dir = Path(ws) / "원본_보관"
    found = history_view.find_original(originals_dir, "지출결의서.pdf")
    check("로그에 남은 원래 이름으로 찾았을 때도 실제 파일을 찾아냄(최선노력)",
          found is not None and found.exists(), str(found))


def test_compare_button_calls_compare_view_with_correct_paths(app):
    print("test_compare_button_calls_compare_view_with_correct_paths")
    ws = _new_tmp()
    src_dir = _new_tmp()
    src = src_dir / "file.pdf"
    _make_dummy_pdf(src)
    r = process_atomic(str(src), _detect(src), str(ws))
    check("처리 성공", r.success)

    dlg = history_view.HistoryDialog(ws)
    dlg.table.selectRow(0)

    calls = []
    original_fn = compare_view.open_result_comparison
    compare_view.open_result_comparison = lambda *a, **kw: calls.append(a)
    from PySide6.QtWidgets import QPushButton
    compare_btn = next(b for b in dlg.findChildren(QPushButton) if b.text().startswith("선택 건"))
    QTest.mouseClick(compare_btn, Qt.LeftButton)
    compare_view.open_result_comparison = original_fn

    check("compare_view.open_result_comparison이 정확히 1번 호출됨", len(calls) == 1, str(calls))
    if calls:
        original_arg, masked_arg = calls[0]
        check("원본 경로가 실제 이동된 원본과 일치", original_arg == r.original_moved_to, original_arg)
        check("결과물 경로가 실제 마스킹 결과와 일치", masked_arg == r.output_path, masked_arg)
    dlg.close()


def test_compare_button_without_selection_shows_message(app):
    print("test_compare_button_without_selection_shows_message")
    ws = _new_tmp()
    dlg = history_view.HistoryDialog(ws)
    row = dlg._selected_row()
    check("선택된 행이 없으면 None을 돌려줌(크래시 없이)", row is None)
    dlg.close()


def test_main_history_entrypoint_runs_without_crash(app):
    print("test_main_history_entrypoint_runs_without_crash")
    ws = _new_tmp()
    src_dir = _new_tmp()
    src = src_dir / "file.pdf"
    _make_dummy_pdf(src)
    r = process_atomic(str(src), _detect(src), str(ws))
    check("사전 처리 성공", r.success)

    # main.open_history()는 QDialog.exec()을 부르므로 헤드리스에서는 자동 닫기 필요
    main_module.open_history(str(ws), _auto_close_after_ms=100)
    check("예외 없이 열리고 자동으로 닫힘(크래시 없음)", True)


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_history_dialog_lists_processed_files,
        test_history_dialog_empty_when_no_log,
        test_find_original_handles_filename_collision,
        test_compare_button_without_selection_shows_message,
        test_main_history_entrypoint_runs_without_crash,
    ]
    for t in tests:
        t(app)
    test_compare_button_calls_compare_view_with_correct_paths(app)

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 처리 이력 조회 테스트 통과 ({len(tests) + 1}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
