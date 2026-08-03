"""
최소 검토 UI (6.3).

- 기본값: 전체 승인 상태(체크됨) — 검토자는 틀린 것만 체크 해제
- "업무상 성명 후보"는 별도로 묶어 보여주되, 기본 마스킹 대상인 건 동일 (6.2/6.3 정책)
- "승인" 버튼을 눌러야 다음 단계(마스킹)로 넘어감

MVP 범위: 사이드바 항목 점프, 단축키, 실행취소(Undo) 등은 향후 확장으로 남겨두고
"전체승인 + 예외 해제"라는 핵심 동작만 구현.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from detector import Finding

# 결과물 파일명([문서유형]_[처리일자]_[일련번호].pdf, 6.6)에 쓰일 값.
# 자동탐지에 맡기지 않고 검토자가 직접 고르게 해 오분류 리스크를 피함 (6.6) --
# 직접 입력도 가능하도록 편집 가능한 콤보박스로 둠.
DOC_TYPES = ["지출결의서", "민원처리결과", "감사결과보고", "일반문서"]


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

        doc_type_row = QHBoxLayout()
        doc_type_row.addWidget(QLabel("문서유형 (결과 파일명에 사용됨):"))
        self.doc_type_combo = QComboBox()
        self.doc_type_combo.setEditable(True)
        self.doc_type_combo.addItems(DOC_TYPES)
        doc_type_row.addWidget(self.doc_type_combo)
        doc_type_row.addStretch()
        layout.addLayout(doc_type_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)

        basic = [f for f in findings if f.group == "기본"]
        business = [f for f in findings if f.group == "업무상성명후보"]

        if basic:
            inner_layout.addWidget(QLabel("<b>개인정보 (기본)</b>"))
            for f in basic:
                self._add_row(inner_layout, f)

        if business:
            inner_layout.addWidget(QLabel(
                "<b>업무상 성명 후보</b> (결재선 등 — 기본은 마스킹 대상, 필요 시 해제)"
            ))
            for f in business:
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
        cb = QCheckBox(f"[{f.type}] {f.value}")
        cb.setChecked(f.approved)
        layout.addWidget(cb)
        self.checkboxes.append((cb, f))

    def _set_all(self, checked: bool):
        for cb, _ in self.checkboxes:
            cb.setChecked(checked)

    def _on_approve(self):
        for cb, f in self.checkboxes:
            f.approved = cb.isChecked()
        # 빈 값으로 파일명이 만들어지지 않도록, 비어있으면 목록의 기본값으로 대체
        self.doc_type = self.doc_type_combo.currentText().strip() or DOC_TYPES[-1]
        self.approved_result = True
        self.accept()


def run_review(
    filename: str, findings: list[Finding], _auto_approve_after_ms: int | None = None
) -> tuple[list[Finding], str] | None:
    """검토 화면을 띄우고, 승인되면 (approved 플래그가 반영된 findings, 문서유형)을,
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
        return findings, window.doc_type
    return None
