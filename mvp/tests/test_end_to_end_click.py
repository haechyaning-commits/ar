"""
end-to-end 테스트: main.process_file()을 실제 Qt 클릭 이벤트로 검토 화면을 조작하며
끝까지(탐지 -> 검토 -> 마스킹 -> 원본이동 -> 로그) 실행해서 전체 파이프라인을 검증한다.

xvfb-run -a python3 mvp/tests/test_end_to_end_click.py 로 실행할 것.
실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

import dictionary_learning
import template_fingerprint
import main as main_module

# 6.2.1 사전 자동학습이 실제 mvp/data/ 사전을 반복 테스트로 오염시키지 않도록 격리
dictionary_learning.DEFAULT_DATA_DIR = Path(tempfile.mkdtemp(prefix="dict_learning_test_"))
# 6.2.2 템플릿 지문도 마찬가지로 실제 mvp/data/template_fingerprints.json을 건드리지 않도록 격리
template_fingerprint.DEFAULT_DATA_DIR = Path(tempfile.mkdtemp(prefix="template_fp_test_"))

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def click_review_dialog_uncheck_first_then_approve():
    """현재 떠있는 모달 검토창을 찾아 첫 체크박스를 해제하고 승인 버튼을 클릭."""
    app = QApplication.instance()
    w = app.activeModalWidget()
    if w is None:
        check("검토창이 떠 있어야 클릭 테스트 가능", False, "activeModalWidget()가 None")
        return
    checkboxes = w.findChildren(QCheckBox)
    if checkboxes:
        first = checkboxes[0]
        QTest.mouseClick(first, Qt.LeftButton, Qt.NoModifier, QPoint(10, first.height() // 2))
    buttons = w.findChildren(QPushButton)
    approve = next(b for b in buttons if b.text().startswith("승인"))
    QTest.mouseClick(approve, Qt.LeftButton)


def test_end_to_end_via_real_clicks():
    print("test_end_to_end_via_real_clicks")
    QApplication.instance() or QApplication([])

    work_src = Path(tempfile.mkdtemp(prefix="e2e_src_"))
    workspace = Path(tempfile.mkdtemp(prefix="e2e_ws_"))
    src = work_src / "지출결의서.pdf"

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "신청인: 김테스트 (010-1234-5678)", fontsize=11, fontname="korea")
    doc.save(src)
    doc.close()

    QTimer.singleShot(300, click_review_dialog_uncheck_first_then_approve)
    # _auto_processor_name: 처리자 이름을 물어보는 QInputDialog는 헤드리스 환경에서
    # 응답할 사람이 없어 그대로 두면 멈춘다 -- 테스트 전용 훅으로 자동 응답.
    # _auto_reprocess_confirm: 이번 실행은 새 파일이라 재실행 확인창 자체가 뜨지
    # 않지만, 다른 테스트가 이 워크스페이스를 재사용하게 되는 경우를 대비해 방어적으로 지정.
    rc = main_module.process_file(str(src), str(workspace),
                                   _auto_processor_name="테스트담당자",
                                   _auto_reprocess_confirm=True)

    check("반환 코드 0(성공)", rc == 0, str(rc))

    masked_files = list((workspace / "마스킹완료").glob("*.pdf"))
    check("마스킹완료/에 결과 파일 1개 생성", len(masked_files) == 1, str(masked_files))

    original_files = list((workspace / "원본_보관").glob("*.pdf"))
    check("원본_보관/에 원본 1개 이동됨", len(original_files) == 1, str(original_files))
    check("원본 소스 위치에는 더 이상 파일이 없음(이동 확인)", not src.exists())

    log_path = workspace / "로그" / "audit_log.csv"
    check("로그 파일 생성됨", log_path.exists())

    if masked_files:
        masked_doc = fitz.open(masked_files[0])
        text = masked_doc[0].get_text(sort=True)
        masked_doc.close()
        # 첫 체크박스("이름: 김테스트")를 실제 클릭으로 해제했으므로 이름은 원문 그대로,
        # 전화번호는 그대로 승인 상태였으므로 마스킹되어야 함
        check("클릭으로 체크 해제한 '이름'은 마스킹 안 되고 원문 그대로 남음", "김테스트" in text, text)
        check("체크 유지된 '전화번호'는 마스킹됨(원문 사라짐)", "010-1234-5678" not in text, text)
        check("전화번호 마스킹 형태 확인", "010-****-5678" in text, text)


def main():
    test_end_to_end_via_real_clicks()

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("end-to-end 클릭 테스트 통과")
    sys.exit(0)


if __name__ == "__main__":
    main()
