"""
원본-마스킹본 비교 뷰 (6.3.2).

두 가지 시점에서 쓰임새가 다름 (설계서 6.3.2):

① 승인 전 미리보기(PreviewCompareDialog): 검토 화면에서 지금 체크된 항목들이
실제로 마스킹되면 어떻게 보일지, 화면에만 검은 오버레이를 그려 시뮬레이션한
뒤 원본과 나란히 보여준다. **실제 파일은 전혀 건드리지 않음** — 5번 계층의
진짜 리댁션과는 완전히 별개이고, 승인 버튼을 눌러야 비로소 실제 처리가
시작된다. 모덜리스(non-modal) 대화상자로 띄워서, 검토 화면에서 체크박스를
켜고 끌 때마다(review_ui.py가 토글 이벤트마다 refresh()를 호출) 오른쪽
미리보기가 실시간으로 갱신되게 함.

② 승인 후 비교(open_result_comparison): 처리가 끝난 뒤 `원본_보관/`의 원본과
`마스킹완료/`의 결과물을 나란히 열어 "정말 이렇게 처리됐는지" 재확인. 아직
"처리 이력 조회 UI"(설계서 11번, 향후 확장)가 없으므로, 이 MVP 범위에서는
처리 성공 직후 main.py가 한 번 물어보고 원하면 열어주는 방식으로 연결한다.

설계상 주의: ②는 `원본_보관/`의 원본(개인정보 포함)을 다시 여는 경로이므로,
설계서 6.3.2/6.6이 지적한 대로 원본 폴더 접근 제한(현재 "권고")의 중요성이
이 기능으로 인해 더 커진다 — 이 코드 자체가 OS 파일 접근 권한을 강제하지는
않으므로, 배포 시 그 권고를 함께 안내해야 함(6.6 참고).

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

from typing import Callable

import fitz  # PyMuPDF
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from detector import Finding
from page_viewer import RENDER_SCALE, render_page_pixmap
from pdf_extract import SpanRef, page_of, rects_for_range


def _pane(title: str) -> tuple[QWidget, QScrollArea, QLabel]:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(QLabel(f"<b>{title}</b>"))
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    image_label = QLabel()
    scroll.setWidget(image_label)
    layout.addWidget(scroll)
    return box, scroll, image_label


class PreviewCompareDialog(QDialog):
    """① 승인 전 미리보기. review_ui.ReviewWindow가 모덜리스로 띄우고,
    체크박스가 바뀔 때마다 refresh()를 호출해 오른쪽 미리보기를 갱신한다."""

    def __init__(
        self, filename: str, input_path: str, full_text: str, spans: list[SpanRef],
        findings: list[Finding], is_checked: Callable[[Finding], bool], start_page: int = 0,
    ):
        super().__init__()
        self.setWindowTitle(f"원본-마스킹본 미리보기 비교 - {filename}")
        self.resize(920, 640)
        self.setModal(False)  # 검토 화면(체크박스)을 계속 조작할 수 있어야 실시간 갱신이 의미 있음

        # review_ui.py가 검토 시작 시 이미 원본을 닫아둔 상태이므로, 미리보기를
        # 그리는 동안만 읽기 전용으로 별도로 다시 연다 (page_viewer.py와 같은 이유/방식)
        self.doc = fitz.open(input_path)
        self.full_text = full_text
        self.spans = spans
        self.findings = findings
        self.is_checked = is_checked
        self.page_index = max(0, min(start_page, self.doc.page_count - 1))

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "⚠ 미리보기입니다 — 실제 파일은 아직 건드리지 않았습니다. "
            "오른쪽은 지금 체크된 항목이 마스킹되면 어떻게 보일지 시뮬레이션한 것이며, "
            "승인 버튼을 눌러야 실제 처리가 시작됩니다."
        ))

        nav_row = QHBoxLayout()
        prev_btn = QPushButton("◀ 이전 페이지")
        prev_btn.clicked.connect(lambda: self._go_page(self.page_index - 1))
        self.page_label = QLabel()
        next_btn = QPushButton("다음 페이지 ▶")
        next_btn.clicked.connect(lambda: self._go_page(self.page_index + 1))
        nav_row.addWidget(prev_btn)
        nav_row.addWidget(self.page_label)
        nav_row.addWidget(next_btn)
        nav_row.addStretch()
        layout.addLayout(nav_row)

        panes_row = QHBoxLayout()
        left_box, self.left_scroll, self.left_image = _pane("원본")
        right_box, self.right_scroll, self.right_image = _pane("마스킹 후 미리보기 (시뮬레이션)")
        panes_row.addWidget(left_box)
        panes_row.addWidget(right_box)
        layout.addLayout(panes_row)

        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._render_page()

    def closeEvent(self, event):
        self.doc.close()
        super().closeEvent(event)

    def _go_page(self, page_index: int):
        if 0 <= page_index < self.doc.page_count:
            self.page_index = page_index
            self._render_page()

    def refresh(self):
        """review_ui.py가 체크박스 토글마다 호출 -- 지금 페이지를 다시 그려
        오른쪽 미리보기(체크 상태 기반 시뮬레이션)를 최신 상태로 유지."""
        self._render_page()

    def _render_page(self):
        page = self.doc[self.page_index]
        self.left_image.setPixmap(render_page_pixmap(page))

        right_pixmap = render_page_pixmap(page)
        painter = QPainter(right_pixmap)
        painter.setBrush(QColor(0, 0, 0))
        painter.setPen(Qt.NoPen)
        for f in self.findings:
            if not self.is_checked(f):
                continue
            if page_of(self.spans, f.start) != self.page_index:
                continue
            if self.full_text[f.start:f.end] != f.value:
                continue  # 방어적 점검: 어긋난 위치는 그리지 않음 (page_viewer.py와 동일한 안전장치)
            for rp in rects_for_range(self.full_text, self.spans, f.start, f.end):
                x0, y0, x1, y1 = rp.rect
                painter.drawRect(QRect(
                    int(x0 * RENDER_SCALE), int(y0 * RENDER_SCALE),
                    max(int((x1 - x0) * RENDER_SCALE), 1), max(int((y1 - y0) * RENDER_SCALE), 1),
                ))
        painter.end()
        self.right_image.setPixmap(right_pixmap)

        self.page_label.setText(f"{self.page_index + 1} / {self.doc.page_count} 페이지")


class SideBySidePdfDialog(QDialog):
    """② 승인 후 비교. 이미 저장이 끝난 두 파일(원본/마스킹본)을 읽기 전용으로
    나란히 열어 보여준다 -- PreviewCompareDialog와 달리 시뮬레이션이 아니라
    실제로 저장된 두 파일을 그대로 보여줌."""

    def __init__(self, title: str, left_path: str, left_label: str, right_path: str, right_label: str):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(920, 640)
        self.left_doc = fitz.open(left_path)
        self.right_doc = fitz.open(right_path)
        self.page_index = 0
        self.page_count = max(self.left_doc.page_count, self.right_doc.page_count)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "이미 처리가 완료된 두 파일을 읽기 전용으로 비교합니다 (원본은 개인정보를 포함하고 있습니다)."
        ))

        nav_row = QHBoxLayout()
        prev_btn = QPushButton("◀ 이전 페이지")
        prev_btn.clicked.connect(lambda: self._go_page(self.page_index - 1))
        self.page_label = QLabel()
        next_btn = QPushButton("다음 페이지 ▶")
        next_btn.clicked.connect(lambda: self._go_page(self.page_index + 1))
        nav_row.addWidget(prev_btn)
        nav_row.addWidget(self.page_label)
        nav_row.addWidget(next_btn)
        nav_row.addStretch()
        layout.addLayout(nav_row)

        panes_row = QHBoxLayout()
        left_box, self.left_scroll, self.left_image = _pane(left_label)
        right_box, self.right_scroll, self.right_image = _pane(right_label)
        panes_row.addWidget(left_box)
        panes_row.addWidget(right_box)
        layout.addLayout(panes_row)

        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._render_page()

    def closeEvent(self, event):
        self.left_doc.close()
        self.right_doc.close()
        super().closeEvent(event)

    def _go_page(self, page_index: int):
        if 0 <= page_index < self.page_count:
            self.page_index = page_index
            self._render_page()

    def _render_page(self):
        if self.page_index < self.left_doc.page_count:
            self.left_image.setPixmap(render_page_pixmap(self.left_doc[self.page_index]))
        else:
            self.left_image.setText("(해당 페이지 없음)")
        if self.page_index < self.right_doc.page_count:
            self.right_image.setPixmap(render_page_pixmap(self.right_doc[self.page_index]))
        else:
            self.right_image.setText("(해당 페이지 없음)")
        self.page_label.setText(f"{self.page_index + 1} / {self.page_count} 페이지")


def open_result_comparison(
    original_path: str, masked_path: str, _auto_close_after_ms: int | None = None,
) -> None:
    """② 처리 완료 직후 main.py가 호출. _auto_close_after_ms는 헤드리스 통합
    테스트 전용 -- 지정하면 그 시간 뒤 대화상자를 자동으로 닫아 사람 입력 없이
    코드 경로를 실행/검증할 수 있게 함(review_ui.run_review의 _auto_approve_after_ms와
    같은 패턴)."""
    dialog = SideBySidePdfDialog(
        "원본-마스킹본 비교", original_path, "원본 (원본_보관/)", masked_path, "마스킹 완료본",
    )
    if _auto_close_after_ms is not None:
        QTimer.singleShot(_auto_close_after_ms, dialog.accept)
    dialog.exec()
