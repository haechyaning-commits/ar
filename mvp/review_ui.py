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
- "승인" 버튼을 눌러야 다음 단계(마스킹)로 넘어감

MVP 범위: 사이드바 항목 점프, 단축키, 실행취소(Undo) 등은 향후 확장으로 남겨두고
"전체승인 + 예외 해제" + "저신뢰 우선 정렬" + "동일 값 일괄 처리" + "페이지 단위
진행 상태 표시" + "수동 추가" + "문서 유형 선택"까지 구현.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMenu,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import dictionary_learning
from detector import CONFIDENCE_LEVELS, Finding, find_occurrences, reload_dictionaries
from output import DOCUMENT_TYPES
from page_viewer import PageViewerDialog
from pdf_extract import SpanRef, page_of


class ReviewWindow(QDialog):
    def __init__(
        self, filename: str, findings: list[Finding], full_text: str = "",
        spans: list[SpanRef] | None = None, total_pages: int = 1, input_path: str = "",
        retry_notice: str | None = None, highlight_spans: set[tuple[int, int]] | None = None,
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
        self.checkboxes: list[tuple[QCheckBox, Finding]] = []
        self.finding_page: dict[int, int] = {}  # id(finding) -> page_index
        self.page_buttons: dict[int, QPushButton] = {}
        self.page_counts: dict[int, int] = {}
        self.page_reviewed: dict[int, bool] = {}  # 스크롤로 실제 화면에 보인 적 있는 페이지
        self.approved_result: bool | None = None  # None=닫힘/취소, True=승인
        self.doc_type: str = DOCUMENT_TYPES[0]

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
        if self.input_path:
            manual_add = QPushButton("직접 추가 (드래그·클릭·텍스트)")
            manual_add.clicked.connect(self._open_manual_add)
            btn_row.addWidget(manual_add)
        approve = QPushButton("승인 (마스킹 진행)")
        approve.clicked.connect(self._on_approve)
        btn_row.addStretch()
        btn_row.addWidget(approve)
        layout.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        # 창이 실제로 화면에 뜨는(=레이아웃이 최종 크기로 확정된) 시점에 첫 검사
        self._check_visible_rows()

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
        if f.cross_validated:
            tags.append("6.2.3 교차검증 반영")
        marker = "⚠[미제거] " if (f.start, f.end) in self.highlight_spans else ""
        cb = QCheckBox(f"{marker}[{f.type}] {f.value} ({', '.join(tags)})")
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
    """
    app = QApplication.instance() or QApplication([])
    window = ReviewWindow(filename, findings, full_text, spans, total_pages, input_path,
                           retry_notice, highlight_spans)
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
