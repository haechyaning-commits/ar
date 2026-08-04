"""
6.3.1 수동 추가 -- PDF 페이지를 화면에 직접 렌더링해서 보여주고, 드래그/클릭/
텍스트 입력으로 자동탐지가 놓친 개인정보를 검토자가 직접 지정해 추가하는 화면.

- 드래그: 화면에서 영역을 직접 지정 (파란 사각형으로 미리보기)
- 클릭: 단어 하나를 빠르게 지정 (거의 움직이지 않은 드래그를 클릭으로 취급)
- 텍스트 직접 입력: 문서 전체에서 완전일치 검색(detector.find_occurrences 재사용),
  여러 곳에서 발견되면 검토자가 개별/전체 선택

드래그/클릭 모두 페이지 이미지 위의 좌표를 pdf_extract.resolve_word_at/resolve_region으로
실제 텍스트의 (start, end)로 되짚는다 -- 영역 안에 인식 가능한 텍스트가 없으면
(이미지/도장 등) 추가하지 않음. 새로 추가된 항목은 review_ui.py의 다른 항목과
완전히 같은 파이프라인(승인 -> 5번 마스킹 계층)을 탄다.
"""
from __future__ import annotations

import fitz  # PyMuPDF
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from detector import Finding, classify_value, find_occurrences
from pdf_extract import SpanRef, page_of, rects_for_range, resolve_region, resolve_word_at

RENDER_SCALE = 1.5
TYPE_OPTIONS = ["이름", "전화번호", "이메일", "계좌번호", "주민등록번호", "여권번호", "주소", "기타"]
# 9번 표: 수동 추가 시 유형 미선택 기본값은 완전마스킹 -- mask_value()의 미지정 유형
# 폴백(전체 마스킹)과 동일하게 동작하도록 "기타"를 기본 선택값으로 둠
DEFAULT_TYPE = "기타"


def render_page_pixmap(page: fitz.Page, scale: float = RENDER_SCALE) -> QPixmap:
    """PDF 페이지를 화면 표시용 QPixmap으로 렌더링. compare_view.py(6.3.2)도
    같은 RENDER_SCALE로 그려야 좌표 변환이 어긋나지 않으므로 여기서 공유.
    scale(신규): review_ui.py의 검토창 내장 캔버스 확대/축소 기능이 RENDER_SCALE
    배수로 다른 배율을 요청할 때 씀 -- 생략하면 기존과 동일하게 RENDER_SCALE."""
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())  # copy() -- pix가 사라진 뒤에도 데이터 유지


class _PageCanvas(QLabel):
    """페이지 이미지를 보여주고 드래그/클릭을 화면(px) 좌표로 알려주는 위젯.
    PDF 좌표 변환은 상위(PageViewerDialog)에서 RENDER_SCALE로 나눠서 처리."""
    selection_made = Signal(int, int, int, int)  # x0, y0, x1, y1 (모두 px)

    def __init__(self, pixmap: QPixmap, existing_rects_px: list[QRect]):
        super().__init__()
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.existing_rects_px = existing_rects_px
        self._drag_start: QPoint | None = None
        self._drag_current: QPoint | None = None

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(230, 140, 0), 2))
        for r in self.existing_rects_px:
            painter.drawRect(r)
        if self._drag_start is not None and self._drag_current is not None:
            painter.setPen(QPen(QColor(30, 110, 230), 2))
            painter.drawRect(QRect(self._drag_start, self._drag_current).normalized())
        painter.end()

    def mousePressEvent(self, event):
        pt = event.position().toPoint()
        self._drag_start = pt
        self._drag_current = pt
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            self._drag_current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_start is None:
            return
        end = event.position().toPoint()
        rect_px = QRect(self._drag_start, end).normalized()
        self._drag_start = None
        self._drag_current = None
        self.update()
        self.selection_made.emit(rect_px.left(), rect_px.top(), rect_px.right(), rect_px.bottom())


class PageViewerDialog(QDialog):
    def __init__(
        self, filename: str, pdf_path: str, full_text: str, spans: list[SpanRef],
        findings: list[Finding], start_page: int = 0,
    ):
        super().__init__()
        self.setWindowTitle(f"수동 추가 - {filename}")
        self.resize(700, 620)
        # 검토 화면(review_ui.py)이 열릴 때는 원본 파일을 이미 닫아둔 상태라,
        # 페이지를 그리는 동안만 별도로 읽기 전용으로 다시 염 (수정 안 함, closeEvent에서 닫음)
        self.doc = fitz.open(pdf_path)
        self.full_text = full_text
        self.spans = spans
        self.findings = findings  # 참조 -- 새 항목을 여기 append (호출한 쪽 목록과 공유)
        self.new_findings: list[Finding] = []
        self.page_index = max(0, min(start_page, self.doc.page_count - 1))

        layout = QVBoxLayout(self)

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

        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("찾을 문자열 입력 (완전일치, 문서 전체 검색)")
        search_btn = QPushButton("찾기")
        search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self.search_box)
        search_row.addWidget(search_btn)
        layout.addLayout(search_row)

        # 검색 결과(6.3.1의 "N곳에서 발견됨" 다중매칭 목록) -- 평소엔 숨겨둠
        self.search_results = QWidget()
        self.search_results_layout = QVBoxLayout(self.search_results)
        self.search_results.setVisible(False)
        layout.addWidget(self.search_results)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("추정된 유형 (자동 감지, 틀렸으면 바꾼 뒤 같은 위치를 다시 선택):"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(TYPE_OPTIONS)
        self.type_combo.setCurrentText(DEFAULT_TYPE)
        type_row.addWidget(self.type_combo)
        type_row.addStretch()
        layout.addLayout(type_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll)

        self.status_label = QLabel(
            "페이지 이미지에서 영역을 드래그하거나 단어를 클릭하면 유형을 자동으로 추정해 추가합니다."
        )
        layout.addWidget(self.status_label)

        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._render_page()

    def closeEvent(self, event):
        self.doc.close()
        super().closeEvent(event)

    # -- 페이지 표시 ------------------------------------------------------
    def _go_page(self, page_index: int):
        if 0 <= page_index < self.doc.page_count:
            self.page_index = page_index
            self._render_page()

    def _render_page(self):
        page = self.doc[self.page_index]
        pixmap = render_page_pixmap(page)

        existing_rects_px = []
        for f in self.findings:
            if page_of(self.spans, f.start) != self.page_index:
                continue
            if self.full_text[f.start:f.end] != f.value:
                continue  # 방어적 점검: 어긋난 위치는 오버레이에 표시하지 않음
            for rp in rects_for_range(self.full_text, self.spans, f.start, f.end):
                x0, y0, x1, y1 = rp.rect
                existing_rects_px.append(QRect(
                    int(x0 * RENDER_SCALE), int(y0 * RENDER_SCALE),
                    max(int((x1 - x0) * RENDER_SCALE), 1), max(int((y1 - y0) * RENDER_SCALE), 1),
                ))

        canvas = _PageCanvas(pixmap, existing_rects_px)
        canvas.selection_made.connect(self._on_selection)
        self.scroll.setWidget(canvas)
        self.page_label.setText(f"{self.page_index + 1} / {self.doc.page_count} 페이지")

    # -- 드래그/클릭 처리 --------------------------------------------------
    def _on_selection(self, x0_px: int, y0_px: int, x1_px: int, y1_px: int):
        page = self.doc[self.page_index]
        x0, y0, x1, y1 = (v / RENDER_SCALE for v in (x0_px, y0_px, x1_px, y1_px))
        if abs(x1 - x0) < 3 and abs(y1 - y0) < 3:
            result = resolve_word_at(self.full_text, page, self.spans, self.page_index, (x0, y0))
        else:
            result = resolve_region(self.full_text, page, self.spans, self.page_index, (x0, y0, x1, y1))

        if result is None:
            self.status_label.setText(
                "선택한 위치에서 텍스트를 찾지 못했습니다 (이미지·도장 등 텍스트가 없는 영역은 아직 지원하지 않습니다)."
            )
            return
        self._add_finding(*result, group="수동추가")

    def _add_finding(self, start: int, end: int, value: str, group: str):
        # 매번 유형을 미리 골라야 하면 여러 유형을 섞어 추가할 때 드롭다운을 계속
        # 바꿔야 해서 번거로우므로, detector.classify_value로 형식을 보고 자동
        # 추정해서 바로 추가한다. 드롭다운은 그 추정 결과를 보여주는 용도로 같이
        # 갱신해두고, 검토자가 직접 골라둔 값이 있으면(추정과 다르면) 그걸 우선함
        # -- 같은 위치를 다시 클릭/드래그하면 지금 드롭다운 값으로 유형을 고친다.
        existing = next((f for f in self.findings if f.start == start and f.end == end), None)
        if existing is not None:
            new_type = self.type_combo.currentText()
            if existing.type == new_type:
                self.status_label.setText(f"'{value}'는 이미 [{new_type}](으)로 추가되어 있습니다.")
                return
            existing.type = new_type
            self.status_label.setText(f"유형을 [{new_type}](으)로 고쳤습니다: {value}")
            self._render_page()
            return

        guessed_type = classify_value(value)
        f = Finding(guessed_type, value, start, end, group=group, approved=True, confidence="낮음", source="수동")
        self.findings.append(f)
        self.new_findings.append(f)
        self.type_combo.setCurrentText(guessed_type)
        self.status_label.setText(
            f"추가됨: [{guessed_type}] {value} (자동 추정 -- 틀렸으면 위 드롭다운에서 유형을 바꾼 뒤 같은 위치를 다시 선택하면 고쳐집니다)"
        )
        self._render_page()  # 방금 추가한 항목도 오버레이에 바로 반영

    # -- 텍스트 직접 입력 ---------------------------------------------------
    def _on_search(self):
        while self.search_results_layout.count():
            item = self.search_results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        value = self.search_box.text().strip()
        if not value:
            self.search_results.setVisible(False)
            return
        occurrences = find_occurrences(self.full_text, value)
        if not occurrences:
            self.search_results.setVisible(True)
            self.search_results_layout.addWidget(QLabel(f"'{value}' 문서 내에서 찾지 못했습니다."))
            return

        self.search_results_layout.addWidget(QLabel(f"'{value}' — {len(occurrences)}곳에서 발견됨:"))
        checkboxes: list[tuple[QCheckBox, tuple[int, int]]] = []
        for start, end in occurrences:
            page_index = page_of(self.spans, start)
            already = any(f.start == start and f.end == end for f in self.findings)
            cb = QCheckBox(f"페이지 {page_index + 1} — {value}" + (" (이미 추가됨)" if already else ""))
            cb.setChecked(not already)
            cb.setEnabled(not already)
            self.search_results_layout.addWidget(cb)
            checkboxes.append((cb, (start, end)))

        add_btn = QPushButton("선택 항목 추가")

        def _confirm():
            for cb, (start, end) in checkboxes:
                if cb.isChecked() and cb.isEnabled():
                    self._add_finding(start, end, value, group="수동추가")
            self.search_results.setVisible(False)

        add_btn.clicked.connect(_confirm)
        self.search_results_layout.addWidget(add_btn)
        self.search_results.setVisible(True)
