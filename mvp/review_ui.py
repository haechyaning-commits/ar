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
- "승인" 버튼을 눌러야 다음 단계(마스킹)로 넘어감

MVP 범위: 사이드바 항목 점프, 단축키, 실행취소(Undo) 등은 향후 확장으로 남겨두고
"전체승인 + 예외 해제" + "저신뢰 우선 정렬"까지만 구현.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from detector import CONFIDENCE_LEVELS, Finding


class ReviewWindow(QDialog):
    def __init__(self, filename: str, findings: list[Finding]):
        super().__init__()
        self.setWindowTitle(f"검토 - {filename}")
        self.resize(560, 480)
        self.findings = findings
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
        inner_layout = QVBoxLayout(inner)

        # 6.3.5: 확신도가 낮은 항목이 위로 오도록 목록 전체를 정렬 (stable sort라
        # 같은 확신도끼리는 기존 문서 내 등장 순서가 그대로 유지됨)
        ordered = sorted(findings, key=lambda f: CONFIDENCE_LEVELS.index(f.confidence))

        inner_layout.addWidget(QLabel(
            "<b>탐지된 개인정보</b> — 확신도 낮은 순 (업무상 성명 후보는 기본 마스킹 대상, 필요 시 해제)"
        ))
        for f in ordered:
            self._add_row(inner_layout, f)

        inner_layout.addStretch()
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

    def _add_row(self, layout: QVBoxLayout, f: Finding):
        tags = [f"확신도: {f.confidence}"]
        if f.group == "업무상성명후보":
            tags.append("업무상 성명 후보")
        if f.cross_validated:
            tags.append("6.2.3 교차검증 반영")
        cb = QCheckBox(f"[{f.type}] {f.value} ({', '.join(tags)})")
        cb.setChecked(f.approved)
        layout.addWidget(cb)
        self.checkboxes.append((cb, f))

    def _set_all(self, checked: bool):
        for cb, _ in self.checkboxes:
            cb.setChecked(checked)

    def _on_approve(self):
        for cb, f in self.checkboxes:
            f.approved = cb.isChecked()
        self.approved_result = True
        self.accept()


def run_review(
    filename: str, findings: list[Finding], _auto_approve_after_ms: int | None = None
) -> list[Finding] | None:
    """검토 화면을 띄우고, 승인되면 approved 플래그가 반영된 findings를,
    취소/닫힘이면 None을 반환.

    _auto_approve_after_ms: 실사용에서는 쓰지 않음. 화면이 없는 환경(headless)에서
    통합 테스트할 때만 사용 — 지정한 시간 뒤 자동으로 승인 버튼을 누른 것처럼 동작.
    """
    app = QApplication.instance() or QApplication([])
    window = ReviewWindow(filename, findings)
    if _auto_approve_after_ms is not None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(_auto_approve_after_ms, window._on_approve)
    window.exec()
    if window.approved_result:
        return findings
    return None
