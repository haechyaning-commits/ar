"""
최소 검토 UI (6.3).

- 기본값: 전체 승인 상태(체크됨) — 검토자는 틀린 것만 체크 해제
- 저신뢰 항목 우선 정렬 (6.3.5): 목록 전체를 탐지 확신도가 낮은 순으로 정렬해,
  검토자가 애매한 항목부터 먼저 살펴볼 수 있도록 함. 확신도는 이미 6.2.3
  교차검증 규칙으로 조정된 값을 그대로 정렬 키로 재사용 — 별도 정렬 로직을
  새로 만들지 않음(6.2.3이 4번 점수를 매기고, 6.3.5는 그 점수를 쓰기만 함)
- "업무상 성명 후보"는 그룹 태그로 구분해 보여주되, 기본 마스킹 대상인 건
  동일 (6.2/6.3 정책) — 확신도 정렬을 그대로 유지하기 위해 별도 섹션으로
  쪼개지 않고 한 목록 안에서 태그로만 표시
- 동일 값 일괄 처리 (6.3.3): 항목을 우클릭하면 문서 전체에서 같은 문자열이
  몇 곳에 더 있는지 찾아(detector.find_occurrences) "전체 선택/전체 해제"를
  제공. 이미 탐지된 위치는 체크만 맞추고, 자동탐지가 놓친 위치는 "전체 선택"을
  눌렀을 때만 새 항목으로 추가(6.3.1 수동 추가와 동일한 파이프라인 재사용 —
  "해제"는 원래 대상이 아니던 위치를 새로 만들 이유가 없어 아무 일도 안 함)
- "승인" 버튼을 눌러야 다음 단계(마스킹)로 넘어감

MVP 범위: 사이드바 항목 점프, 단축키, 실행취소(Undo) 등은 향후 확장으로 남겨두고
"전체승인 + 예외 해제" + "저신뢰 우선 정렬" + "동일 값 일괄 처리"까지만 구현.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMenu,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from detector import CONFIDENCE_LEVELS, Finding, find_occurrences


class ReviewWindow(QDialog):
    def __init__(self, filename: str, findings: list[Finding], full_text: str = ""):
        super().__init__()
        self.setWindowTitle(f"검토 - {filename}")
        self.resize(560, 480)
        self.findings = findings
        self.full_text = full_text
        self.checkboxes: list[tuple[QCheckBox, Finding]] = []
        self.approved_result: bool | None = None  # None=닫힘/취소, True=승인

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"<b>{filename}</b> — 탐지된 개인정보 {len(findings)}건. "
            "기본적으로 전부 마스킹 대상입니다. 마스킹하면 안 되는 항목만 체크 해제하세요."
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.inner_layout = QVBoxLayout(inner)

        # 6.3.5: 확신도가 낮은 항목이 위로 오도록 목록 전체를 정렬 (stable sort라
        # 같은 확신도끼리는 기존 문서 내 등장 순서가 그대로 유지됨)
        ordered = sorted(findings, key=lambda f: CONFIDENCE_LEVELS.index(f.confidence))

        self.inner_layout.addWidget(QLabel(
            "<b>탐지된 개인정보</b> — 확신도 낮은 순 (업무상 성명 후보는 기본 마스킹 대상, 필요 시 해제)"
            " · 항목 우클릭 시 동일 값 일괄 선택/해제"
        ))
        for f in ordered:
            self._add_row(f)

        self.inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        select_all = QPushButton("전체 선택")
        select_all.clicked.connect(lambda: self._set_all(True))
        deselect_all = QPushButton("전체 해제")
        deselect_all.clicked.connect(lambda: self._set_all(False))
        approve = QPushButton("승인 (마스킹 진행)")
        approve.clicked.connect(self._on_approve)
        btn_row.addWidget(select_all)
        btn_row.addWidget(deselect_all)
        btn_row.addStretch()
        btn_row.addWidget(approve)
        layout.addLayout(btn_row)

    def _make_checkbox(self, f: Finding) -> QCheckBox:
        tags = [f"확신도: {f.confidence}"]
        if f.group == "업무상성명후보":
            tags.append("업무상 성명 후보")
        if f.group == "동일값추가":
            tags.append("동일 값 일괄 추가")
        if f.cross_validated:
            tags.append("6.2.3 교차검증 반영")
        cb = QCheckBox(f"[{f.type}] {f.value} ({', '.join(tags)})")
        cb.setChecked(f.approved)
        cb.setContextMenuPolicy(Qt.CustomContextMenu)
        cb.customContextMenuRequested.connect(lambda pos, box=cb, finding=f: self._show_context_menu(box, finding, pos))
        return cb

    def _add_row(self, f: Finding):
        """초기 구성 때 목록 맨 끝에 항목을 추가."""
        cb = self._make_checkbox(f)
        self.inner_layout.addWidget(cb)
        self.checkboxes.append((cb, f))

    def _add_row_before_stretch(self, f: Finding):
        """구성이 끝난 뒤(우클릭 등으로) 동적으로 추가할 때, 맨 아래 stretch보다 위에 넣는다."""
        cb = self._make_checkbox(f)
        self.inner_layout.insertWidget(self.inner_layout.count() - 1, cb)
        self.checkboxes.append((cb, f))

    def _set_all(self, checked: bool):
        for cb, _ in self.checkboxes:
            cb.setChecked(checked)

    def _show_context_menu(self, cb: QCheckBox, f: Finding, pos):
        # 6.3.3: 문서 전체에서 같은 문자열이 몇 곳에 더 있는지 찾아 일괄 선택/해제 제공.
        # full_text가 없으면(테스트 등) 현재 목록에 있는 항목끼리만이라도 동작하게 함.
        occurrences = find_occurrences(self.full_text, f.value) if self.full_text else \
            sorted({(ff.start, ff.end) for _, ff in self.checkboxes if ff.value == f.value})
        if len(occurrences) <= 1:
            return  # 문서 내 이 값이 하나뿐이면 일괄 처리할 게 없음

        menu = QMenu(self)
        header = menu.addAction(f"문서 내 동일 값 {len(occurrences)}건 발견")
        header.setEnabled(False)
        menu.addSeparator()
        action_select = menu.addAction("전체 선택")
        action_deselect = menu.addAction("전체 해제")
        chosen = menu.exec(cb.mapToGlobal(pos))
        if chosen is action_select:
            self._apply_to_occurrences(f, occurrences, True)
        elif chosen is action_deselect:
            self._apply_to_occurrences(f, occurrences, False)

    def _apply_to_occurrences(self, f: Finding, occurrences: list[tuple[int, int]], checked: bool):
        existing = {(ff.start, ff.end): cb for cb, ff in self.checkboxes if ff.value == f.value}
        for start, end in occurrences:
            key = (start, end)
            if key in existing:
                existing[key].setChecked(checked)
            elif checked:
                # 자동탐지가 못 잡은 위치는 "전체 선택"할 때만 새 항목으로 추가
                # (6.3.1 수동 추가와 동일한 파이프라인: 승인 후 5번 마스킹 계층으로
                # 그대로 흘러감). "전체 해제"는 애초에 대상이 아니던 위치를 새로
                # 만들 이유가 없으므로 그냥 건너뜀
                new_f = Finding(f.type, f.value, start, end, group="동일값추가",
                                 approved=True, confidence="낮음")
                self.findings.append(new_f)
                self._add_row_before_stretch(new_f)

    def _on_approve(self):
        for cb, f in self.checkboxes:
            f.approved = cb.isChecked()
        self.approved_result = True
        self.accept()


def run_review(
    filename: str, findings: list[Finding], full_text: str = "",
    _auto_approve_after_ms: int | None = None,
) -> list[Finding] | None:
    """검토 화면을 띄우고, 승인되면 approved 플래그가 반영된 findings를,
    취소/닫힘이면 None을 반환.

    full_text: 6.3.3(동일 값 일괄 처리)이 문서 전체에서 같은 값을 찾을 때 씀.
    _auto_approve_after_ms: 실사용에서는 쓰지 않음. 화면이 없는 환경(headless)에서
    통합 테스트할 때만 사용 — 지정한 시간 뒤 자동으로 승인 버튼을 누른 것처럼 동작.
    """
    app = QApplication.instance() or QApplication([])
    window = ReviewWindow(filename, findings, full_text)
    if _auto_approve_after_ms is not None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(_auto_approve_after_ms, window._on_approve)
    window.exec()
    if window.approved_result:
        return window.findings
    return None
