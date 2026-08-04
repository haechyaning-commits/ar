"""
처리 이력 조회 UI (설계서 11번 "도구 내 처리 이력 조회 UI" 향후 확장 후보의
최소 구현, 실사용자 아이디어 8).

audit_log.csv(6.7)를 직접 열지 않아도, 도구 안에서 지난 처리 내역을 표로
보고 원본_보관/마스킹완료의 실제 파일을 compare_view(6.3.2 ②)로 열람할 수
있게 한다. 새 로그 스키마를 만들지 않고 기존 audit_log.csv를 그대로 읽기만
한다.

⚠ pipeline._unique_dest가 파일명 충돌 시 원본_보관/에 "(1)" 등을 붙이는데,
audit_log에는 그 충돌 처리 전 원래 이름이 남는다 -- 그래서 원본을 못 찾으면
같은 stem으로 시작하는 후보를 최선노력으로 찾는다(완벽한 역추적은 아님,
이력 조회는 어디까지나 참고용).

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건).
"""
from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

import compare_view
from output import FOLDER_NAMES

LOG_FILE_NAME = "audit_log.csv"

# audit_log.LOG_FIELDS 순서와 다르게, 이력 화면에서 사람이 보기 좋은 순서로 재배치
COLUMNS = [
    "timestamp", "processor", "original_filename", "output_filename",
    "masked_counts", "review_seconds", "rotated_text_warning", "hidden_content_warning",
]
COLUMN_LABELS = [
    "처리일시", "처리자", "원본 파일명", "결과물 파일명",
    "마스킹 건수", "검토 소요(초)", "회전텍스트 경고", "숨김콘텐츠 경고",
]


def read_log_rows(logs_dir: Path) -> list[dict]:
    log_path = Path(logs_dir) / LOG_FILE_NAME
    if not log_path.exists():
        return []
    with open(log_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_original(originals_dir: Path, original_filename: str) -> Path | None:
    """pipeline._unique_dest가 충돌 시 파일명 뒤에 (1), (2)...를 붙이므로,
    로그에 남은 원래 이름 그대로가 없으면 같은 stem으로 시작하는 후보를 찾는다."""
    direct = originals_dir / original_filename
    if direct.exists():
        return direct
    stem = Path(original_filename).stem
    suffix = Path(original_filename).suffix
    candidates = sorted(originals_dir.glob(f"{stem}*{suffix}"))
    return candidates[0] if candidates else None


class HistoryDialog(QDialog):
    def __init__(self, workspace_dir: str | Path):
        super().__init__()
        self.setWindowTitle("처리 이력 조회")
        self.resize(860, 420)
        base = Path(workspace_dir)
        self.originals_dir = base / FOLDER_NAMES["originals"]
        self.masked_dir = base / FOLDER_NAMES["masked"]
        self.rows = read_log_rows(base / FOLDER_NAMES["logs"])
        self._display_rows = list(reversed(self.rows))  # 최신 처리가 위로

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>처리 이력</b> — 총 {len(self.rows)}건 (최신순)"))

        self.table = QTableWidget(len(self._display_rows), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMN_LABELS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for row_idx, row in enumerate(self._display_rows):
            for col_idx, key in enumerate(COLUMNS):
                value = row.get(key, "")
                if key in ("rotated_text_warning", "hidden_content_warning"):
                    value = "⚠ 있음" if value == "True" else ""
                self.table.setItem(row_idx, col_idx, QTableWidgetItem(value))
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        compare_btn = QPushButton("선택 건 원본-결과물 비교")
        compare_btn.clicked.connect(self._open_comparison)
        btn_row.addWidget(compare_btn)
        btn_row.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _selected_row(self) -> dict | None:
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        return self._display_rows[indexes[0].row()]

    def _open_comparison(self):
        row = self._selected_row()
        if row is None:
            QMessageBox.information(self, "안내", "먼저 목록에서 항목을 선택하세요.")
            return
        original_path = find_original(self.originals_dir, row["original_filename"])
        masked_path = self.masked_dir / row["output_filename"]
        if original_path is None or not masked_path.exists():
            QMessageBox.warning(
                self, "안내",
                "원본 또는 결과물 파일을 찾을 수 없습니다 (이동되었거나 삭제됐을 수 있습니다).",
            )
            return
        compare_view.open_result_comparison(str(original_path), str(masked_path))
