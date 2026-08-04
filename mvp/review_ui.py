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
- 페이지 단위 진행 상태 표시 (6.3.4): 이 UI에는 실제 PDF 페이지 렌더링/이동이
  없어(전체 문서를 한 화면의 스크롤 목록으로 보여줌) 설계서 원안의 "페이지 넘김
  이벤트"가 그대로 존재하지 않음. 대신 "스크롤해서 그 페이지의 항목이 화면에
  실제로 보였다"를 페이지 넘김의 등가물로 삼아 자동으로 검토완료 표시함(검토자가
  버튼을 눌러야 하는 별도 동작 아님 -- 목록을 훑어보는 것 자체가 진행 상황을
  채움). 승인 시점에 아직 화면에 스크롤되어 보인 적 없는 페이지가 있으면 경고를
  띄우고 승인을 차단. 진행바의 페이지 버튼은 클릭하면 해당 페이지의 첫 항목으로
  점프하는 용도로만 쓰고(눌러야 검토완료 처리되는 게 아님), 배지로 페이지별
  탐지 건수를 보여줌. "안 본 페이지만 보기" 필터·경고 페이지 색 강조는 아직
  미구현(향후 확장).
- 수동 추가 (6.3.1): "직접 추가" 버튼을 누르면 page_viewer.PageViewerDialog가
  뜸 -- PDF 페이지를 이미지로 렌더링해서 보여주고, 드래그(영역 지정)/클릭(단어
  하나)/텍스트 직접 입력(문서 전체 검색, 6.3.3과 같은 find_occurrences 재사용)
  세 가지 방식으로 자동탐지가 놓친 항목을 추가할 수 있음. 대화상자가 닫히면
  거기서 추가된 항목들이 이 검토 목록에도 그대로 반영됨(같은 findings 리스트를
  공유하므로 이미 findings에는 들어가 있고, 체크박스 행/페이지 진행바만 새로 반영).
- 문서 유형 선택: 결과 파일명 규칙(6.6, `[문서유형]_[처리일자]_[일련번호].pdf`)에
  쓸 문서유형을 검토자가 직접 고름 -- 자동 판별하지 않음(설계서 6.6: "문서유형은
  검토 단계에서 사람이 직접 선택")
- 사전 자동 학습(6.2.1): 검토자가 이미 하는 행동(자동탐지 이름 체크 해제 = 제외
  후보, 수동으로 이름 추가 = 성씨 후보)만으로 조용히 후보를 누적(`dictionary_learning`).
  확인 팝업 없이 통보만 하고, 임계값(3회) 도달 시 실제 사전에 반영 + `detector.reload_dictionaries()`로
  즉시 재적용(배치 처리 중 다음 파일부터 바로 반영되도록)
- 자체검증 실패 후 재시도: main.py가 self-check 실패로 이 화면을 다시 띄울 때
  `retry_notice`/`highlight_spans`를 넘기면, 안내 문구와 함께 안 지워진 항목에
  "⚠[미제거]" 표시를 붙여 검토자가 바로 찾아 재확인할 수 있게 함(8번 리스크의
  "실패 후 후속 흐름" 요구사항)
- 원본-마스킹본 미리보기 비교(6.3.2 ①): "원본-마스킹본 미리보기 비교" 버튼을
  누르면 모덜리스(non-modal) 창이 뜸 -- 왼쪽은 원본 페이지 그대로, 오른쪽은
  지금 체크된 항목들을 화면에만 검은 사각형으로 그려 마스킹되면 어떻게 보일지
  시뮬레이션(`compare_view.PreviewCompareDialog`, 실제 파일은 손대지 않음).
  모덜리스라 검토 화면에서 체크박스를 계속 조작할 수 있고, 토글할 때마다
  이 화면이 열려있으면 실시간으로 다시 그림. 6.3.2 ②(승인 후 원본_보관/ 결과물
  비교)는 처리 성공 직후 main.py가 물어보는 방식으로 연결(`compare_view.open_result_comparison`)
- 문서 템플릿 지문 인식(6.2.2): main.py가 detect_all() 결과에 저장된 템플릿
  지문 기반 후보(`template_fingerprint.suggest_candidates`)를 미리 합쳐서
  넘겨주므로, 이 화면 입장에서는 그룹 "템플릿후보" 태그만 붙여 구분해서 보여줌.
  승인 시점(`_on_approve`)에 그때 승인된 항목 위치를 다시 지문으로 조용히
  기록(`template_fingerprint.record_template`) -- 사전 자동학습(6.2.1)과 같은
  결로, 검토자가 이미 하는 승인 행동만으로 조용히 축적되고 별도 확인을 묻지 않음
- "승인" 버튼을 눌러야 다음 단계(마스킹)로 넘어감
- 실사용자 편의 기능(6.3.6): 항목마다 "🔍 확대" 버튼을 붙여 눌렀을 때 그 항목
  주변만 실제 PDF에서 고배율로 다시 렌더링해 보여줌(원안의 "클릭 시 자동 확대"를
  이 UI 구조 -- 체크박스 목록이라 항목 클릭 자체는 이미 체크 토글에 쓰이므로,
  같은 클릭에 확대까지 얹지 않고 별도 버튼으로 분리 -- 에 맞게 재현). "다음
  미확정 항목" 버튼/단축키(Ctrl+J)는 확신도가 "높음"이 아닌(=6.3.5 저신뢰
  정렬 대상) 항목만 순서대로 스크롤+포커스 이동, 정규식으로 확실히 잡히는
  항목(전화번호 등)은 건너뜀

MVP 범위: 실행취소(Undo) 등은 향후 확장으로 남겨두고 "전체승인 + 예외 해제" +
"저신뢰 우선 정렬" + "동일 값 일괄 처리" + "페이지 단위 진행 상태 표시" +
"수동 추가" + "문서 유형 선택" + "미리보기 비교" + "항목 확대/다음 미확정 항목
순회"까지 구현.
"""
from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMenu,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import compare_view
import dictionary_learning
import template_fingerprint
from detector import CONFIDENCE_LEVELS, Finding, find_occurrences, reload_dictionaries
from output import DOCUMENT_TYPES
from page_viewer import PageViewerDialog
from pdf_extract import SpanRef, page_of, rects_for_range

# 6.3.6 "항목 클릭 시 자동 확대" -- 숫자 한두 자리 차이가 중요한 항목(주민번호 등)의
# 오독을 줄이는 게 목적이므로 화면 표시(RENDER_SCALE=1.5)보다 훨씬 높은 배율로 다시 렌더링
ZOOM_FACTOR = 4.0
ZOOM_PADDING = 24.0  # PDF 좌표(pt) 기준 항목 주변 여백 -- 문맥이 조금 보여야 어떤 라벨인지 알 수 있음


class _ZoomDialog(QDialog):
    """6.3.6: 사이드바 항목을 확대해서 보여주는 팝업. 화면 표시용 저해상도
    렌더링과 별개로, 이 항목의 위치만 clip해서 고배율로 새로 렌더링 -- 전체
    페이지를 고배율로 그린 뒤 자르는 것보다 가볍고, PyMuPDF의 clip 인자가
    바로 이 용도로 지원됨."""

    def __init__(self, f: Finding, pdf_path: str, full_text: str, spans: list[SpanRef]):
        super().__init__()
        self.setWindowTitle(f"확대 보기 - [{f.type}] {f.value}")

        layout = QVBoxLayout(self)
        rects = rects_for_range(full_text, spans, f.start, f.end) if pdf_path else []
        if not rects or full_text[f.start:f.end] != f.value:
            layout.addWidget(QLabel("이 항목의 위치를 찾을 수 없습니다."))
        else:
            doc = fitz.open(pdf_path)
            page_index = rects[0].page_index
            page = doc[page_index]
            x0 = min(r.rect[0] for r in rects) - ZOOM_PADDING
            y0 = min(r.rect[1] for r in rects) - ZOOM_PADDING
            x1 = max(r.rect[2] for r in rects) + ZOOM_PADDING
            y1 = max(r.rect[3] for r in rects) + ZOOM_PADDING
            clip = fitz.Rect(max(x0, 0), max(y0, 0), min(x1, page.rect.width), min(y1, page.rect.height))
            pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM_FACTOR, ZOOM_FACTOR), clip=clip)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            image_label = QLabel()
            image_label.setPixmap(QPixmap.fromImage(img.copy()))
            layout.addWidget(QLabel(f"페이지 {page_index + 1} — [{f.type}] {f.value}"))
            layout.addWidget(image_label)
            doc.close()

        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class ReviewWindow(QDialog):
    def __init__(
        self, filename: str, findings: list[Finding], full_text: str = "",
        spans: list[SpanRef] | None = None, total_pages: int = 1, input_path: str = "",
        retry_notice: str | None = None, highlight_spans: set[tuple[int, int]] | None = None,
        page_sizes: list[tuple[float, float]] | None = None,
    ):
        super().__init__()
        self.setWindowTitle(f"검토 - {filename}")
        self.resize(560, 520)
        self.filename = filename
        self.findings = findings
        self.full_text = full_text
        self.spans = spans or []
        self.total_pages = max(total_pages, 1)
        self.input_path = input_path
        self.highlight_spans = highlight_spans or set()
        self.page_sizes = page_sizes or []
        self.checkboxes: list[tuple[QCheckBox, Finding]] = []
        self.finding_page: dict[int, int] = {}  # id(finding) -> page_index
        self.page_buttons: dict[int, QPushButton] = {}
        self.page_counts: dict[int, int] = {}
        self.page_reviewed: dict[int, bool] = {}  # 스크롤로 실제 화면에 보인 적 있는 페이지
        self.approved_result: bool | None = None  # None=닫힘/취소, True=승인
        self.doc_type: str = DOCUMENT_TYPES[0]
        self._preview_dialog: compare_view.PreviewCompareDialog | None = None  # 6.3.2 ①

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"<b>{filename}</b> — 탐지된 개인정보 {len(findings)}건. "
            "기본적으로 전부 마스킹 대상입니다. 마스킹하면 안 되는 항목만 체크 해제하세요."
        ))

        if retry_notice:
            notice = QLabel(f"⚠ {retry_notice}")
            notice.setStyleSheet("color: #b00; font-weight: bold;")
            notice.setWordWrap(True)
            layout.addWidget(notice)

        doc_type_row = QHBoxLayout()
        doc_type_row.addWidget(QLabel("문서 유형 (결과 파일명에 사용):"))
        self.doc_type_combo = QComboBox()
        self.doc_type_combo.addItems(DOCUMENT_TYPES)
        self.doc_type_combo.currentTextChanged.connect(self._on_doc_type_changed)
        doc_type_row.addWidget(self.doc_type_combo)
        doc_type_row.addStretch()
        layout.addLayout(doc_type_row)

        for f in findings:
            self.finding_page[id(f)] = page_of(self.spans, f.start)
            self.page_counts[self.finding_page[id(f)]] = self.page_counts.get(self.finding_page[id(f)], 0) + 1

        layout.addLayout(self._build_progress_bar())
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #b00;")
        layout.addWidget(self.status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll = scroll
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

        # 6.3.4: 스크롤될 때마다 화면에 보인 항목들의 페이지를 자동으로 검토완료 처리.
        # (여기서 바로 _check_visible_rows()를 부르면 안 됨 -- 창이 실제로 표시되기
        # 전이라 레이아웃이 아직 최종 크기로 확정되지 않았고, 그 상태로 검사하면
        # 뷰포트가 실제보다 훨씬 크게 잡혀서 전부 "보임"으로 잘못 표시됨. 창이 실제로
        # 뜨는 시점(showEvent)에 처음 검사하고, 이후엔 스크롤될 때마다 재검사)
        scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._check_visible_rows())

        btn_row = QHBoxLayout()
        select_all = QPushButton("전체 선택")
        select_all.clicked.connect(lambda: self._set_all(True))
        deselect_all = QPushButton("전체 해제")
        deselect_all.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(select_all)
        btn_row.addWidget(deselect_all)
        next_low_confidence = QPushButton("다음 미확정 항목 (Ctrl+J)")
        next_low_confidence.setShortcut(QKeySequence("Ctrl+J"))
        next_low_confidence.clicked.connect(self._jump_to_next_low_confidence)
        btn_row.addWidget(next_low_confidence)
        if self.input_path:
            manual_add = QPushButton("직접 추가 (드래그·클릭·텍스트)")
            manual_add.clicked.connect(self._open_manual_add)
            btn_row.addWidget(manual_add)
            preview_compare = QPushButton("원본-마스킹본 미리보기 비교")
            preview_compare.clicked.connect(self._open_preview_compare)
            btn_row.addWidget(preview_compare)
        approve = QPushButton("승인 (마스킹 진행)")
        approve.clicked.connect(self._on_approve)
        btn_row.addStretch()
        btn_row.addWidget(approve)
        layout.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        # 창이 실제로 화면에 뜨는(=레이아웃이 최종 크기로 확정된) 시점에 첫 검사
        self._check_visible_rows()

    def closeEvent(self, event):
        # 6.3.2 ①: 검토 화면이 닫히는데 모덜리스 미리보기 비교 창이 열려있으면
        # 같이 닫아서(fitz.Document를 계속 열어둔 채) 남지 않게 함
        if self._preview_dialog is not None:
            self._preview_dialog.close()
        super().closeEvent(event)

    # -- 6.3.4 페이지 단위 진행 상태 표시 ------------------------------------
    def _build_progress_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.progress_label = QLabel()
        row.addWidget(self.progress_label)
        row.addSpacing(8)
        for page_index in range(self.total_pages):
            self.page_reviewed[page_index] = False
            btn = QPushButton(self._page_button_text(page_index))
            btn.setFixedWidth(44)
            btn.setToolTip(f"페이지 {page_index + 1}로 이동 (스크롤해서 보면 자동으로 검토완료 표시됨)")
            btn.clicked.connect(lambda _checked=False, p=page_index: self._jump_to_page(p))
            self.page_buttons[page_index] = btn
            row.addWidget(btn)
        row.addStretch()
        self._refresh_page_buttons()
        return row

    def _page_button_text(self, page_index: int) -> str:
        count = self.page_counts.get(page_index, 0)
        return f"{page_index + 1}\n({count}건)" if count else f"{page_index + 1}"

    def _refresh_page_buttons(self):
        for page_index, btn in self.page_buttons.items():
            btn.setText(self._page_button_text(page_index))
            btn.setStyleSheet("background-color: #b7e3b7;" if self.page_reviewed.get(page_index) else "")
        self._update_progress_label()

    def _update_progress_label(self):
        done = sum(1 for v in self.page_reviewed.values() if v)
        total = len(self.page_reviewed)
        bar = "■" * done + "□" * (total - done)
        self.progress_label.setText(f"[{bar}]  {done} / {total} 페이지 검토 완료")

    def _jump_to_page(self, page_index: int):
        # "진행바 클릭 시 해당 페이지로 즉시 이동" -- 그 페이지의 첫 항목으로 스크롤.
        # 검토완료 표시 자체는 스크롤 결과로 _check_visible_rows가 자동으로 처리함
        # (이 버튼을 누르는 행위 자체가 검토완료를 만드는 게 아님)
        for cb, f in self.checkboxes:
            if self.finding_page.get(id(f)) == page_index:
                self.scroll.ensureWidgetVisible(cb)
                break

    def _check_visible_rows(self):
        """스크롤 영역에 실제로 보이는(교차하는) 항목들의 페이지를 검토완료로 표시.
        한 번 검토완료된 페이지는 다시 스크롤해서 안 보이게 되어도 그대로 유지."""
        viewport = self.scroll.viewport()
        viewport_rect = viewport.rect()
        changed = False
        for cb, f in self.checkboxes:
            page = self.finding_page.get(id(f))
            if page is None or self.page_reviewed.get(page):
                continue
            top_left = cb.mapTo(viewport, QPoint(0, 0))
            widget_rect = QRect(top_left, cb.size())
            if viewport_rect.intersects(widget_rect):
                self.page_reviewed[page] = True
                changed = True
        if changed:
            self.status_label.setText("")
            self._refresh_page_buttons()

    def _mark_all_pages_reviewed(self):
        """실사용에서는 스크롤로 자동 처리됨 -- 헤드리스 테스트 전용 보조 메서드
        (테스트 환경엔 실제로 스크롤할 화면이 없어 가시성 감지가 의미 없으므로)."""
        for page_index in self.page_reviewed:
            self.page_reviewed[page_index] = True
        self._refresh_page_buttons()

    # -------------------------------------------------------------------

    def _make_checkbox(self, f: Finding) -> QCheckBox:
        tags = [f"확신도: {f.confidence}"]
        if f.group == "업무상성명후보":
            tags.append("업무상 성명 후보")
        if f.group == "동일값추가":
            tags.append("동일 값 일괄 추가")
        if f.group == "수동추가":
            tags.append("6.3.1 수동 추가")
        if f.group == "템플릿후보":
            tags.append("6.2.2 템플릿 지문 후보")
        if f.cross_validated:
            tags.append("6.2.3 교차검증 반영")
        marker = "⚠[미제거] " if (f.start, f.end) in self.highlight_spans else ""
        cb = QCheckBox(f"{marker}[{f.type}] {f.value} ({', '.join(tags)})")
        cb.setChecked(f.approved)
        cb.setContextMenuPolicy(Qt.CustomContextMenu)
        cb.customContextMenuRequested.connect(lambda pos, box=cb, finding=f: self._show_context_menu(box, finding, pos))
        cb.toggled.connect(self._on_checkbox_toggled)  # 6.3.2 ①: 열려있는 미리보기 비교 창을 실시간 갱신
        return cb

    def _make_row_widget(self, cb: QCheckBox, f: Finding) -> QWidget:
        """6.3.6: input_path가 있으면(원본을 다시 열 수 있으면) 체크박스 옆에
        "🔍 확대" 버튼을 붙인 행을 만든다 -- 체크박스 클릭 자체는 이미 토글에
        쓰이므로, 확대는 같은 클릭이 아니라 별도 버튼으로 분리."""
        if not self.input_path:
            return cb
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(cb, 1)
        zoom_btn = QPushButton("🔍 확대")
        zoom_btn.setFixedWidth(70)
        zoom_btn.clicked.connect(lambda _checked=False, finding=f: self._show_zoomed_view(finding))
        row_layout.addWidget(zoom_btn)
        return row

    def _show_zoomed_view(self, f: Finding):
        _ZoomDialog(f, self.input_path, self.full_text, self.spans).exec()

    def _jump_to_next_low_confidence(self):
        """6.3.6 "다음 미확정 항목" 순회: 확신도가 "높음"(정규식 등으로 이미
        확실한 항목)이 아닌 것만 순서대로 스크롤+포커스 이동. 별도 커서를 두지
        않고 "지금 포커스된 체크박스"를 기준으로 다음 후보를 찾음 -- 수동 추가
        등으로 목록이 중간에 늘어나도 매번 현재 화면 상태 기준으로 다시 계산되므로
        어긋나지 않음."""
        candidates = [cb for cb, f in self.checkboxes if f.confidence != "높음"]
        if not candidates:
            self.status_label.setText("확인이 필요한(확신도 낮음/중간) 항목이 없습니다.")
            return
        current = self.focusWidget()  # 이 창 자체의 포커스 체인 -- 앱 전역(QApplication.focusWidget())보다 안정적
        start_idx = (candidates.index(current) + 1) % len(candidates) if current in candidates else 0
        target = candidates[start_idx]
        self.scroll.ensureWidgetVisible(target)
        target.setFocus()

    def _add_row(self, f: Finding):
        """초기 구성 때 목록 맨 끝에 항목을 추가."""
        cb = self._make_checkbox(f)
        self.inner_layout.addWidget(self._make_row_widget(cb, f))
        self.checkboxes.append((cb, f))

    def _add_row_before_stretch(self, f: Finding):
        """구성이 끝난 뒤(우클릭 등으로) 동적으로 추가할 때, 맨 아래 stretch보다 위에 넣는다."""
        cb = self._make_checkbox(f)
        self.inner_layout.insertWidget(self.inner_layout.count() - 1, self._make_row_widget(cb, f))
        self.checkboxes.append((cb, f))

        # 새 항목도 페이지 진행바 배지에 반영 (동일 값 일괄 처리로 새로 찾은 위치일 수 있음)
        page_index = page_of(self.spans, f.start)
        self.finding_page[id(f)] = page_index
        self.page_counts[page_index] = self.page_counts.get(page_index, 0) + 1
        if page_index in self.page_buttons:
            self._refresh_page_buttons()
        self._check_visible_rows()  # 지금 화면에 바로 보이는 위치에 추가됐을 수 있음

    def _set_all(self, checked: bool):
        for cb, _ in self.checkboxes:
            cb.setChecked(checked)

    def _on_doc_type_changed(self, text: str):
        self.doc_type = text

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
                                 approved=True, confidence="낮음", source="수동")
                self.findings.append(new_f)
                self._add_row_before_stretch(new_f)

    def _open_manual_add(self):
        # 6.3.1: 아직 검토완료로 안 넘어간 페이지가 있으면 거기서 시작 -- 어차피
        # 승인 전에 한 번은 봐야 하는 페이지이므로 자연스러운 시작점
        start_page = next((p for p, seen in sorted(self.page_reviewed.items()) if not seen), 0)
        dlg = PageViewerDialog(self.filename, self.input_path, self.full_text, self.spans,
                                self.findings, start_page)
        dlg.exec()
        # dlg.findings는 self.findings와 같은 리스트(참조 공유)라 이미 반영돼 있음 --
        # 여기서는 체크박스 행/페이지 진행바만 새로 추가된 만큼 따라잡으면 됨
        for f in dlg.new_findings:
            self._add_row_before_stretch(f)
            if f.type == "이름" and f.value:
                # 6.2.1: 수동으로 이름을 추가하는 행동을 "성씨 후보" 신호로 기록.
                # SURNAMES는 성씨(대개 1글자)만 담는 사전이라 전체 이름이 아니라
                # 첫 글자만 후보로 씀 (남궁/황보 같은 2글자 성씨는 이 근사치로는 못 배움).
                if dictionary_learning.record_candidate(f.value[0], "이름후보"):
                    reload_dictionaries()
                    self.status_label.setText(f"[사전 자동학습] '{f.value[0]}' 성씨 후보로 반영했습니다.")

    # -- 6.3.2 ① 승인 전 원본-마스킹본 미리보기 비교 -------------------------
    def _is_checked(self, f: Finding) -> bool:
        for cb, ff in self.checkboxes:
            if ff is f:
                return cb.isChecked()
        return f.approved

    def _on_checkbox_toggled(self, _checked: bool):
        if self._preview_dialog is not None:
            self._preview_dialog.refresh()

    def _open_preview_compare(self):
        if self._preview_dialog is not None:
            # 이미 열려있으면 새로 만들지 않고 앞으로 가져오기만 함
            self._preview_dialog.raise_()
            self._preview_dialog.activateWindow()
            return
        start_page = next((p for p, seen in sorted(self.page_reviewed.items()) if not seen), 0)
        dlg = compare_view.PreviewCompareDialog(
            self.filename, self.input_path, self.full_text, self.spans,
            self.findings, self._is_checked, start_page,
        )
        dlg.finished.connect(lambda _result: setattr(self, "_preview_dialog", None))
        self._preview_dialog = dlg
        dlg.show()

    def _on_approve(self):
        self._check_visible_rows()  # 마지막으로 화면에 있는 상태를 승인 직전 한 번 더 반영
        unreviewed = [p + 1 for p, seen in sorted(self.page_reviewed.items()) if not seen]
        if unreviewed:
            # 6.3.4: 스크롤로 실제로 보인 적 없는 페이지가 남아있으면 승인 자체를 차단
            pages_str = ", ".join(str(p) for p in unreviewed)
            self.status_label.setText(
                f"⚠ {len(unreviewed)}페이지를 아직 확인하지 않았습니다 (페이지 {pages_str}). "
                "목록을 스크롤해서 해당 페이지 항목을 확인한 뒤 다시 승인하세요."
            )
            return
        for cb, f in self.checkboxes:
            newly_unchecked = f.approved and not cb.isChecked()
            f.approved = cb.isChecked()
            if newly_unchecked and f.type == "이름" and f.source == "자동":
                # 6.2.1: 자동탐지된 이름을 체크 해제하는 행동을 "제외어 후보" 신호로 기록
                if dictionary_learning.record_candidate(f.value, "제외"):
                    reload_dictionaries()
        self.doc_type = self.doc_type_combo.currentText()
        # 6.2.2: 승인된(=실제로 마스킹될) 항목 위치를 이 문서의 템플릿 지문으로 조용히
        # 축적 -- 마스킹/저장이 이후 단계에서 실패하더라도, 검토자가 이 위치를
        # 확정했다는 사실 자체가 지문으로서는 유효한 신호이므로 여기서 기록
        approved = [f for f in self.findings if f.approved]
        template_fingerprint.record_template(self.full_text, self.spans, self.page_sizes, approved)
        self.approved_result = True
        self.accept()


@dataclass
class ReviewResult:
    findings: list[Finding]
    doc_type: str


def run_review(
    filename: str, findings: list[Finding], full_text: str = "",
    spans: list[SpanRef] | None = None, total_pages: int = 1, input_path: str = "",
    _auto_approve_after_ms: int | None = None,
    retry_notice: str | None = None, highlight_spans: set[tuple[int, int]] | None = None,
    page_sizes: list[tuple[float, float]] | None = None,
) -> ReviewResult | None:
    """검토 화면을 띄우고, 승인되면 approved 플래그가 반영된 findings와 선택한
    문서 유형을 ReviewResult로, 취소/닫힘이면 None을 반환.

    full_text/spans: 6.3.3(동일 값 일괄 처리)과 6.3.4(페이지 단위 진행 상태
    표시)가 각각 문서 전체 검색과 페이지 판별에 씀.
    total_pages: 6.3.4 진행바에 표시할 전체 페이지 수.
    input_path: 6.3.1(수동 추가) 화면이 PDF 페이지를 다시 렌더링할 때 필요한
    원본 파일 경로. 비어 있으면 "직접 추가" 버튼 자체를 표시하지 않음.
    _auto_approve_after_ms: 실사용에서는 쓰지 않음. 화면이 없는 환경(headless)에서
    통합 테스트할 때만 사용 — 지정한 시간 뒤 모든 페이지를 확인 표시하고
    승인 버튼을 누른 것처럼 동작(6.3.4 도입 후에도 자동 테스트가 막히지 않도록).
    retry_notice/highlight_spans: 자체 재검증 실패 후 이 화면으로 복귀할 때
    main.py가 넘겨주는 안내 문구와 "안 지워진" 항목의 (start, end) 위치.
    page_sizes: 6.2.2 템플릿 지문 기록에 필요한 페이지별 (width, height).
    비어 있으면(테스트 등) 지문 기록은 조용히 건너뜀.
    """
    app = QApplication.instance() or QApplication([])
    window = ReviewWindow(filename, findings, full_text, spans, total_pages, input_path,
                           retry_notice, highlight_spans, page_sizes)
    if _auto_approve_after_ms is not None:
        from PySide6.QtCore import QTimer

        def _auto():
            window._mark_all_pages_reviewed()
            window._on_approve()

        QTimer.singleShot(_auto_approve_after_ms, _auto)
    window.exec()
    if window.approved_result:
        return ReviewResult(window.findings, window.doc_type)
    return None
