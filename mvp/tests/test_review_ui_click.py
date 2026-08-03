"""
검토 UI(review_ui.py) 클릭 테스트 -- 내부 메서드를 직접 호출하지 않고
실제 Qt 마우스 클릭/키 이벤트(QTest)를 위젯에 보내서 화면에서 클릭했을 때와
동일하게 반응하는지 확인한다.

디스플레이 없는 환경에서 실행하려면 Xvfb로 가상 디스플레이를 띄워야 한다:
    xvfb-run -a python3 mvp/tests/test_review_ui_click.py

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from detector import detect_all
from review_ui import ReviewWindow

SAMPLE = (
    "지출결의서\n"
    "신청인: 김테스트 (010-1234-5678)\n"
    "주민등록번호: 900101-1234568\n"
    "입금계좌: 국민은행 123456-04-789012 (예금주: 김테스트)\n"
    "이메일: test.dummy@example.com\n"
    "주소: 서울특별시 강남구 테헤란로 123\n"
    "기안자: 이감사 / 검토: 박팀장 / 결재: 최과장\n"
)

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def click_checkbox(cb):
    """체크박스는 위젯 중앙(텍스트 라벨 위)을 클릭하면 토글이 안 되는 경우가 있어
    (실측으로 확인된 함정) 왼쪽 인디케이터 좌표를 명시적으로 클릭한다."""
    QTest.mouseClick(cb, Qt.LeftButton, Qt.NoModifier, QPoint(10, cb.height() // 2))


def click_button(window, text_prefix: str):
    btn = next(b for b in window.findChildren(QPushButton) if b.text().startswith(text_prefix))
    QTest.mouseClick(btn, Qt.LeftButton)


def run_scenario(app, findings, driver):
    """ReviewWindow를 띄우고, driver(window)를 이벤트 루프 안에서 실행한 뒤 결과를 반환."""
    window = ReviewWindow("_dummy_audit_file.pdf", findings)
    QTimer.singleShot(150, lambda: driver(window))
    window.exec()
    return window


# ---------------------------------------------------------------------------
def test_default_all_checked(app):
    print("test_default_all_checked")
    findings = detect_all(SAMPLE)
    window = run_scenario(app, findings, lambda w: click_button(w, "승인"))
    check("승인 결과 True", window.approved_result is True)
    check("전부 approved=True (기본값)", all(f.approved for f in findings))


def test_uncheck_individual_item(app):
    print("test_uncheck_individual_item")
    findings = detect_all(SAMPLE)
    unchecked = {}

    def driver(w):
        # 6.3.5(저신뢰 항목 우선 정렬)로 체크박스 표시 순서가 findings 리스트 순서와
        # 다를 수 있으므로, checkboxes[0]이 가리키는 실제 Finding 객체를 기준으로 확인한다.
        cb, f = w.checkboxes[0]
        unchecked["f"] = f
        check(f"클릭 전 기본 체크 상태 ({f.type}:{f.value})", cb.isChecked())
        click_checkbox(cb)
        check("클릭 후 체크 해제됨", not cb.isChecked())
        click_button(w, "승인")

    window = run_scenario(app, findings, driver)
    check("승인 결과 True", window.approved_result is True)
    check("체크 해제한 항목만 approved=False", unchecked["f"].approved is False)
    check("나머지는 전부 approved=True",
          all(f.approved for f in findings if f is not unchecked["f"]))


def test_select_all_button_after_manual_uncheck(app):
    print("test_select_all_button_after_manual_uncheck")
    findings = detect_all(SAMPLE)

    def driver(w):
        click_checkbox(w.checkboxes[0][0])
        click_checkbox(w.checkboxes[1][0])
        check("두 항목 해제됨", not w.checkboxes[0][0].isChecked() and not w.checkboxes[1][0].isChecked())
        click_button(w, "전체 선택")
        check("전체 선택 클릭 후 모두 체크됨", all(cb.isChecked() for cb, _ in w.checkboxes))
        click_button(w, "승인")

    window = run_scenario(app, findings, driver)
    check("승인 결과 True", window.approved_result is True)
    check("전체 선택 클릭 후 전부 approved=True", all(f.approved for f in findings))


def test_deselect_all_button(app):
    print("test_deselect_all_button")
    findings = detect_all(SAMPLE)

    def driver(w):
        click_button(w, "전체 해제")
        check("전체 해제 클릭 후 모두 체크 해제됨", all(not cb.isChecked() for cb, _ in w.checkboxes))
        click_button(w, "승인")

    window = run_scenario(app, findings, driver)
    check("승인 결과 True", window.approved_result is True)
    check("전체 해제 후 승인 -> 전부 approved=False", all(not f.approved for f in findings))


def test_cancel_close_does_not_mutate_findings(app):
    print("test_cancel_close_does_not_mutate_findings")
    findings = detect_all(SAMPLE)

    def driver(w):
        click_checkbox(w.checkboxes[0][0])  # 체크 UI 상태만 바뀜, approved 필드는 아직 반영 전
        QTest.keyClick(w, Qt.Key_Escape)  # 승인 없이 닫기 (검토 취소)

    window = run_scenario(app, findings, driver)
    check("취소 시 approved_result는 None", window.approved_result is None)
    check("취소 시 findings.approved는 원래 기본값(True) 그대로 -- 미승인 조작이 반영되면 안 됨",
          all(f.approved for f in findings))


def test_business_name_group_rendered_separately(app):
    print("test_business_name_group_rendered_separately")
    findings = detect_all(SAMPLE)
    window = ReviewWindow("_dummy_audit_file.pdf", findings)

    from PySide6.QtWidgets import QLabel
    label_texts = [lbl.text() for lbl in window.findChildren(QLabel)]
    check("'개인정보 (기본)' 그룹 라벨 존재", any("개인정보" in t for t in label_texts))
    check("'업무상 성명 후보' 그룹 라벨 존재", any("업무상 성명 후보" in t for t in label_texts))

    business_names = {f.value for f in findings if f.group == "업무상성명후보"}
    basic_names = {f.value for f in findings if f.group == "기본"}
    check("업무상 성명 후보에 결재선 이름(이감사/박팀장/최과장) 포함",
          business_names == {"이감사", "박팀장", "최과장"}, str(business_names))
    check("기본 그룹에 신청인 이름(김테스트) 포함", "김테스트" in basic_names, str(basic_names))
    window.close()


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_default_all_checked,
        test_uncheck_individual_item,
        test_select_all_button_after_manual_uncheck,
        test_deselect_all_button,
        test_cancel_close_does_not_mutate_findings,
        test_business_name_group_rendered_separately,
    ]
    for t in tests:
        t(app)

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 클릭 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
