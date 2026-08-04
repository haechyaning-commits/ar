"""
6.2.1 "되돌리기" 화면 UI 테스트 -- dictionary_learning.get_recent_promotions()/
revert_promotion()이 review_ui.py의 "최근 사전 자동학습 반영" 패널과 실제로
맞물려 동작하는지 실제 Qt 클릭 이벤트로 확인한다.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import detector
import dictionary_learning
from detector import reload_dictionaries
from review_ui import ReviewWindow

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def _isolated_data_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="dict_learning_revert_test_"))
    dictionary_learning.DEFAULT_DATA_DIR = d
    reload_dictionaries()  # detector도 이 빈 사전으로 리셋
    return d


def _promote(word: str, direction: str):
    """임계값(3회)만큼 record_candidate를 호출해 실제로 사전에 반영시킨다."""
    promoted = False
    for _ in range(dictionary_learning.THRESHOLD):
        promoted = dictionary_learning.record_candidate(word, direction) or promoted
    return promoted


def find_promotion_row_button(window) -> QPushButton | None:
    return next((b for b in window.findChildren(QPushButton) if b.text() == "되돌리기"), None)


# ---------------------------------------------------------------------------
def test_panel_hidden_when_no_recent_promotions(app):
    print("test_panel_hidden_when_no_recent_promotions")
    _isolated_data_dir()
    window = ReviewWindow("doc.pdf", [])
    window.show()
    check("최근 반영 내역이 없으면 패널이 숨겨짐", not window.promotions_widget.isVisible())
    window.close()


def test_panel_shows_promotion_and_revert_removes_it(app):
    print("test_panel_shows_promotion_and_revert_removes_it")
    data_dir = _isolated_data_dir()
    promoted = _promote("테", "제외")
    check("3회 반복 후 실제로 사전에 반영됨", promoted)
    check("반영된 단어가 exclude_words.txt에 실제로 들어감",
          "테" in {w for w in (data_dir / "exclude_words.txt").read_text(encoding="utf-8").splitlines() if w})

    window = ReviewWindow("doc.pdf", [])
    window.show()  # isVisible()은 최상위 창이 실제로 show()된 뒤에만 조상 체인을 따라 True가 됨
    check("반영 직후 새로 뜬 검토 창에 되돌리기 패널이 보임", window.promotions_widget.isVisible())
    labels = [lbl.text() for lbl in window.findChildren(QLabel)]
    check("패널에 반영된 단어가 표시됨", any("테" in t and "제외" in t for t in labels), str(labels))

    revert_btn = find_promotion_row_button(window)
    check("되돌리기 버튼이 실제로 존재함", revert_btn is not None)
    QTest.mouseClick(revert_btn, Qt.LeftButton)

    check("되돌리기 후 exclude_words.txt에서 단어가 제거됨",
          "테" not in {w for w in (data_dir / "exclude_words.txt").read_text(encoding="utf-8").splitlines() if w})
    check("되돌리기 후 detector의 EXCLUDE_WORDS도 즉시 갱신됨(reload_dictionaries)", "테" not in detector.EXCLUDE_WORDS)
    check("되돌리기 후 패널에서 해당 행이 사라짐(다시 숨겨짐)", not window.promotions_widget.isVisible())
    check("상태 메시지로 되돌렸음을 안내함", "되돌렸습니다" in window.status_label.text(), window.status_label.text())
    window.close()


def test_revert_does_not_immediately_repromote(app):
    print("test_revert_does_not_immediately_repromote")
    # revert_promotion은 쌓여있던 후보 기록도 초기화하므로, 되돌린 직후
    # 같은 세션에서 또 3회를 채워야 재반영된다 (다음 파일에서 또 우연히
    # 체크 해제 1~2번만 해도 바로 재반영되는 오염을 막기 위함).
    data_dir = _isolated_data_dir()
    _promote("총무", "제외")
    dictionary_learning.revert_promotion("총무", "제외")
    reload_dictionaries()
    check("되돌린 직후에는 후보 카운트가 초기화되어 1회만으로는 재반영 안 됨",
          not dictionary_learning.record_candidate("총무", "제외"))
    check("사전에도 없음", "총무" not in {w for w in (data_dir / "exclude_words.txt").read_text(encoding="utf-8").splitlines() if w})


def test_surname_promotion_via_manual_add_refreshes_panel_live(app):
    print("test_surname_promotion_via_manual_add_refreshes_panel_live")
    # 실제 PageViewerDialog(수동 추가 화면)를 띄우지 않고, review_ui.py가 그
    # 콜백 안에서 하는 것과 동일한 절차(record_candidate -> reload -> 패널 갱신)를
    # 창이 열린 채로 직접 실행해, "창을 닫지 않고도 바로 되돌리기 목록에 뜨는지"를 확인.
    _isolated_data_dir()
    window = ReviewWindow("doc.pdf", [])
    window.show()
    check("시작 시점엔 패널이 비어있음", not window.promotions_widget.isVisible())

    promoted = _promote("공", "이름후보")
    check("성씨 후보 3회 반복 후 반영됨", promoted)
    window._refresh_promotions_panel()  # 6.3.1 수동 추가 콜백이 실제로 호출하는 것과 동일

    check("같은 창 안에서 바로 되돌리기 패널에 반영됨(창을 새로 열지 않아도 됨)",
          window.promotions_widget.isVisible())
    check("성씨 사전에도 실제로 반영됨", any(s == "공" for s in detector.SURNAMES))
    window.close()


def main():
    app = QApplication.instance() or QApplication([])
    tests = [
        test_panel_hidden_when_no_recent_promotions,
        test_panel_shows_promotion_and_revert_removes_it,
        test_revert_does_not_immediately_repromote,
        test_surname_promotion_via_manual_add_refreshes_panel_live,
    ]
    for t in tests:
        t(app)

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 되돌리기 UI 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
