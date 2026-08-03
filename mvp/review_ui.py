"""
최소 검토 UI (6.3).

- 기본값: 전체 승인 상태(체크됨) — 검토자는 틀린 것만 체크 해제
- "업무상 성명 후보"는 별도로 묶어 보여주되, 기본 마스킹 대상인 건 동일 (6.2/6.3 정책)
- 수동 추가(6.3.1): 텍스트 직접 입력 방식만 지원 -- 드래그/클릭 방식은 화면에
  렌더링된 PDF 페이지 위에서 실제 마우스 조작이 필요해 디스플레이 없는 이
  개발 환경에서는 만들어도 검증이 안 되므로 보류
- "승인" 버튼을 눌러야 다음 단계(마스킹)로 넘어감

MVP 범위: 사이드바 항목 점프, 단축키, 실행취소(Undo), 드래그/클릭 수동 추가는
향후 확장으로 남겨두고 "전체승인 + 예외 해제 + 텍스트 기반 수동 추가"까지 구현.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from detector import Finding


def confirm_reprocess(filename: str, reasons: list[str], _auto_confirm: bool | None = None) -> bool:
    """6.4: 이미 처리된 것으로 보이는 파일을 다시 처리할지 확인.
    완전 차단이 아니라 확인만 거치면 재처리를 허용 (마스킹 정책 변경 등으로
    재작업이 필요한 실무 상황을 막지 않기 위함).

    _auto_confirm: 헤드리스 테스트용 -- 지정하면 다이얼로그를 띄우지 않고
    바로 이 값을 반환.
    """
    if _auto_confirm is not None:
        return _auto_confirm
    app = QApplication.instance() or QApplication([])
    text = (
        f"'{filename}'은(는) 이미 처리된 파일로 보입니다:\n- " + "\n- ".join(reasons)
        + "\n\n다시 처리하시겠습니까?"
    )
    reply = QMessageBox.question(
        None, "재처리 확인", text,
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
    )
    return reply == QMessageBox.Yes

# 결과물 파일명([문서유형]_[처리일자]_[일련번호].pdf, 6.6)에 쓰일 값.
# 자동탐지에 맡기지 않고 검토자가 직접 고르게 해 오분류 리스크를 피함 (6.6) --
# 직접 입력도 가능하도록 편집 가능한 콤보박스로 둠.
DOC_TYPES = ["지출결의서", "민원처리결과", "감사결과보고", "일반문서"]

# 수동 추가(6.3.1) 시 선택하는 유형. "기타"를 고르면(또는 아무 것도 못 고르는
# 상황이면) masker.mask_value()가 모르는 유형이라 완전마스킹으로 처리됨 --
# 이게 설계서가 요구하는 "미선택 시 기본값 완전마스킹"과 같은 효과를 냄.
MANUAL_TYPES = ["이름", "전화번호", "이메일", "계좌번호", "카드번호", "주민등록번호", "여권번호", "주소", "기타"]


def find_occurrences(
    text: str, needle: str, exclude_spans: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    """text 안에서 needle이 등장하는 모든(겹치지 않는) 위치를 찾되, 이미
    탐지된 항목과 위치가 같은 건 제외(중복 추가 방지). Qt에 의존하지 않아
    디스플레이 없는 환경에서도 그대로 단위 테스트 가능."""
    if not needle:
        return []
    spans = []
    start = 0
    while (idx := text.find(needle, start)) != -1:
        span = (idx, idx + len(needle))
        if span not in exclude_spans:
            spans.append(span)
        start = idx + len(needle)
    return spans


class ReviewWindow(QDialog):
    def __init__(
        self, filename: str, findings: list[Finding], full_text: str,
        retry_notice: str | None = None,
        highlight_spans: set[tuple[int, int]] | None = None,
        initial_doc_type: str | None = None,
    ):
        super().__init__()
        self.setWindowTitle(f"검토 - {filename}")
        self.resize(560, 560)
        self.findings = findings
        self.full_text = full_text
        self.highlight_spans = highlight_spans or set()
        self.checkboxes: list[tuple[QCheckBox, Finding]] = []
        self.approved_result: bool | None = None  # None=닫힘/취소, True=승인

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"<b>{filename}</b> — 탐지된 개인정보 {len(findings)}건. "
            "기본적으로 전부 마스킹 대상입니다. 마스킹하면 안 되는 항목만 체크 해제하세요."
        ))

        if retry_notice:
            # 6.5.1: 재검증 실패 후 복귀 -- 실제 값은 보여주지 않고(6.5.3) 건수/안내만 표시
            notice_label = QLabel(f"⚠ {retry_notice}")
            notice_label.setStyleSheet("color: #b00020; font-weight: bold;")
            layout.addWidget(notice_label)

        doc_type_row = QHBoxLayout()
        doc_type_row.addWidget(QLabel("문서유형 (결과 파일명에 사용됨):"))
        self.doc_type_combo = QComboBox()
        self.doc_type_combo.setEditable(True)
        self.doc_type_combo.addItems(DOC_TYPES)
        if initial_doc_type:
            # 재검증 실패 후 검토 화면 복귀(6.5.1) 시 이전에 고른 문서유형이
            # 목록 첫 값으로 초기화되던 문제 -- 이전 선택을 그대로 이어받음
            self.doc_type_combo.setCurrentText(initial_doc_type)
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

        # 수동 추가된 항목은 별도 하위 레이아웃에 붙여 넣음 (기존 목록의 addStretch()
        # 앞에 끼워 넣는 계산을 피하기 위한 구조 -- 항상 이 레이아웃 끝에 addWidget)
        self.manual_items_layout = QVBoxLayout()
        self._manual_header_added = False
        inner_layout.addLayout(self.manual_items_layout)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        layout.addLayout(self._build_manual_add_section())

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

    def _build_manual_add_section(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.addWidget(QLabel(
            "<b>수동 추가</b> — 자동탐지가 놓친 항목을 텍스트로 찾아 추가 (6.3.1)"
        ))

        search_row = QHBoxLayout()
        self.manual_text_input = QLineEdit()
        self.manual_text_input.setPlaceholderText("찾을 텍스트 입력")
        self.manual_type_combo = QComboBox()
        self.manual_type_combo.addItems(MANUAL_TYPES)
        search_btn = QPushButton("찾기")
        search_btn.clicked.connect(self._on_search_manual)
        search_row.addWidget(self.manual_text_input)
        search_row.addWidget(self.manual_type_combo)
        search_row.addWidget(search_btn)
        box.addLayout(search_row)

        self.manual_result_label = QLabel("")
        box.addWidget(self.manual_result_label)

        self.manual_result_list = QListWidget()
        self.manual_result_list.setVisible(False)
        self.manual_result_list.setMaximumHeight(100)
        box.addWidget(self.manual_result_list)

        manual_btn_row = QHBoxLayout()
        self.manual_select_all_btn = QPushButton("전체 선택")
        self.manual_select_all_btn.clicked.connect(lambda: self._set_manual_result_all(True))
        self.manual_deselect_all_btn = QPushButton("전체 해제")
        self.manual_deselect_all_btn.clicked.connect(lambda: self._set_manual_result_all(False))
        self.manual_add_btn = QPushButton("선택 추가")
        self.manual_add_btn.clicked.connect(self._on_add_manual)
        for b in (self.manual_select_all_btn, self.manual_deselect_all_btn, self.manual_add_btn):
            b.setVisible(False)
            manual_btn_row.addWidget(b)
        box.addLayout(manual_btn_row)
        return box

    def _toggle_manual_controls(self, visible: bool):
        for w in (
            self.manual_result_list, self.manual_select_all_btn,
            self.manual_deselect_all_btn, self.manual_add_btn,
        ):
            w.setVisible(visible)

    def _on_search_manual(self):
        needle = self.manual_text_input.text().strip()
        self.manual_result_list.clear()

        if not needle:
            self.manual_result_label.setText("찾을 텍스트를 입력하세요.")
            self._toggle_manual_controls(False)
            return

        existing_spans = {(f.start, f.end) for f in self.findings}
        spans = find_occurrences(self.full_text, needle, existing_spans)
        if not spans:
            self.manual_result_label.setText(
                "일치하는 항목을 찾지 못했습니다 (이미 탐지된 항목은 제외됨)."
            )
            self._toggle_manual_controls(False)
            return

        self.manual_result_label.setText(f"{len(spans)}곳에서 발견됨 — 추가할 항목을 선택하세요.")
        for start, end in spans:
            ctx_start, ctx_end = max(0, start - 15), min(len(self.full_text), end + 15)
            snippet = self.full_text[ctx_start:ctx_end].replace("\n", " ")
            item = QListWidgetItem(f"...{snippet}...")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, (start, end))
            self.manual_result_list.addItem(item)
        self._toggle_manual_controls(True)

    def _set_manual_result_all(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.manual_result_list.count()):
            self.manual_result_list.item(i).setCheckState(state)

    def _on_add_manual(self):
        needle = self.manual_text_input.text().strip()
        mtype = self.manual_type_combo.currentText()

        if not self._manual_header_added:
            self.manual_items_layout.addWidget(QLabel("<b>수동 추가된 항목</b>"))
            self._manual_header_added = True

        added = 0
        for i in range(self.manual_result_list.count()):
            item = self.manual_result_list.item(i)
            if item.checkState() != Qt.Checked:
                continue
            start, end = item.data(Qt.UserRole)
            f = Finding(mtype, needle, start, end, group="기본", approved=True, source="수동")
            self.findings.append(f)
            self._add_row(self.manual_items_layout, f)
            added += 1

        self.manual_result_list.clear()
        self._toggle_manual_controls(False)
        self.manual_text_input.clear()
        self.manual_result_label.setText(f"{added}건 추가됨." if added else "선택된 항목이 없습니다.")

    def _add_row(self, layout: QVBoxLayout, f: Finding):
        marker = "[수동]" if f.source == "수동" else ""
        if (f.start, f.end) in self.highlight_spans:
            marker = "⚠[미제거]" + marker
        cb = QCheckBox(f"{marker}[{f.type}] {f.value}")
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
    filename: str, findings: list[Finding], full_text: str,
    _auto_approve_after_ms: int | None = None,
    retry_notice: str | None = None,
    highlight_spans: set[tuple[int, int]] | None = None,
    initial_doc_type: str | None = None,
) -> tuple[list[Finding], str] | None:
    """검토 화면을 띄우고, 승인되면 (승인/수동추가 반영된 findings, 문서유형)을,
    취소/닫힘이면 None을 반환.

    full_text: 수동 추가(6.3.1) 시 텍스트 검색 대상이 되는 문서 전체 텍스트.
    retry_notice / highlight_spans: 자체 재검증 실패 후 검토 화면으로 복귀할 때
    (6.5.1) 사용 -- 실제 값은 보여주지 않고 문구 + 항목 표시로만 안내 (6.5.3).
    initial_doc_type: 재시도 시 이전에 고른 문서유형을 이어받기 위한 값.
    _auto_approve_after_ms: 실사용에서는 쓰지 않음. 화면이 없는 환경(headless)에서
    통합 테스트할 때만 사용 — 지정한 시간 뒤 자동으로 승인 버튼을 누른 것처럼 동작.
    """
    app = QApplication.instance() or QApplication([])
    window = ReviewWindow(
        filename, findings, full_text, retry_notice, highlight_spans, initial_doc_type,
    )
    if _auto_approve_after_ms is not None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(_auto_approve_after_ms, window._on_approve)
    window.exec()
    if window.approved_result:
        return window.findings, window.doc_type
    return None
