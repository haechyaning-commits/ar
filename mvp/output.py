"""
출력 계층: 파일명 규칙(6.6) + 폴더 구조(6.6) + 요약 리포트(6.8)
+ 재실행 방지 판별(6.4) + 처리자 식별(6.7).

감사 로그(6.7)와 원본 이동은 이 모듈이 아니라 pipeline.py(원자적 트랜잭션 +
롤백)와 audit_log.py(rotated_text_warning/hidden_content_warning 등 안전
관련 플래그까지 남기는 CSV 스키마)가 담당한다 -- 이 모듈은 그 둘이 갖고 있지
않던 파일명 규칙/재실행 방지/요약 리포트/처리자 식별만 보탠다.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import getpass
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF


def app_base_dir() -> Path:
    """출력 폴더(원본_보관/마스킹완료/요약리포트/로그)의 기준 위치 (6.6).

    입력 파일이 어느 폴더에 있든, 결과는 항상 프로그램(.exe) 옆 한 곳에 모여야
    감사 이력을 한 로그로 관리할 수 있음 — 그래서 입력 파일 경로가 아니라
    "프로그램 자신의 위치"를 기준으로 함.
    PyInstaller로 패키징된 .exe에서는 sys.executable이 실제 실행 파일 경로를
    가리키므로 그걸 사용하고(임시 압축해제 경로인 sys._MEIPASS가 아님에 주의),
    일반 파이썬 스크립트로 실행할 때는 이 파일이 있는 폴더를 사용함.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

# 처리완료 표시 마커 (6.4) — 개인정보가 아닌 고정 문자열, PDF 표준 메타데이터(keywords)에 남김
PROCESSED_MARKER = "MaskingTool-Processed:true"

FOLDER_NAMES = {
    "originals": "원본_보관",
    "masked": "마스킹완료",
    "reports": "요약리포트",
    "logs": "로그",
}

DOCUMENT_TYPES = ["지출결의서", "출장", "민원", "계약", "기타"]


@dataclass
class OutputFolders:
    base: Path
    originals: Path
    masked: Path
    reports: Path
    logs: Path


def ensure_folders(base_dir: Path) -> OutputFolders:
    originals = base_dir / FOLDER_NAMES["originals"]
    masked = base_dir / FOLDER_NAMES["masked"]
    reports = base_dir / FOLDER_NAMES["reports"]
    logs = base_dir / FOLDER_NAMES["logs"]
    for folder in (originals, masked, reports, logs):
        folder.mkdir(parents=True, exist_ok=True)
    return OutputFolders(base_dir, originals, masked, reports, logs)


# ---------------------------------------------------------------------------
# 재실행(중복 마스킹) 방지 (6.4)
# ---------------------------------------------------------------------------
def is_already_processed(input_path: str | Path) -> bool:
    """① 마스킹완료/ 폴더 경로 또는 ② PDF 안의 비-개인정보 마커로 판별."""
    path = Path(input_path)
    if path.parent.name == FOLDER_NAMES["masked"]:
        return True
    try:
        doc = fitz.open(str(path))
        marker_found = PROCESSED_MARKER in (doc.metadata.get("keywords") or "")
        doc.close()
        return marker_found
    except Exception:
        return False


def mark_processed_file(output_path: str | Path) -> None:
    """저장된 마스킹본을 다시 열어 처리완료 마커를 남김 (메타데이터 제거 이후에 호출해야 함)."""
    doc = fitz.open(str(output_path))
    doc.set_metadata({"keywords": PROCESSED_MARKER})
    tmp_path = str(output_path) + ".marktmp"
    doc.save(tmp_path)
    doc.close()
    Path(tmp_path).replace(output_path)


# ---------------------------------------------------------------------------
# 파일명 규칙 (6.6): [문서유형]_[처리일자]_[일련번호].pdf
# ---------------------------------------------------------------------------
_SERIAL_RE_TEMPLATE = r"^.+_{date}_(\d{{3}})\.pdf$"


def next_serial(masked_dir: Path, date_str: str) -> int:
    pattern = re.compile(_SERIAL_RE_TEMPLATE.format(date=re.escape(date_str)))
    max_serial = 0
    for f in masked_dir.glob(f"*_{date_str}_*.pdf"):
        m = pattern.match(f.name)
        if m:
            max_serial = max(max_serial, int(m.group(1)))
    return max_serial + 1


def build_output_filename(doc_type: str, date_str: str, serial: int) -> str:
    return f"{doc_type}_{date_str}_{serial:03d}.pdf"


# ---------------------------------------------------------------------------
# 처리자 식별 (6.7)
# ---------------------------------------------------------------------------
def _processor_config_path(logs_dir: Path) -> Path:
    return logs_dir / ".processor_name"


def get_saved_processor_name(logs_dir: Path) -> str | None:
    p = _processor_config_path(logs_dir)
    if p.exists():
        name = p.read_text(encoding="utf-8").strip()
        return name or None
    return None


def save_processor_name(logs_dir: Path, name: str) -> None:
    _processor_config_path(logs_dir).write_text(name, encoding="utf-8")


def default_processor_name() -> str:
    # Windows 로그인 계정명 (실제 배포 환경 기준, 6.7)
    return getpass.getuser()


# ---------------------------------------------------------------------------
# 처리 결과 요약 리포트 (6.8)
# ---------------------------------------------------------------------------
def write_summary_report(reports_dir: Path, record: dict, masked_counts: dict[str, int]) -> Path:
    stem = Path(record["결과물파일명"]).stem
    report_path = reports_dir / f"{stem}_요약.txt"
    counts_lines = "\n".join(f"  - {k}: {v}건" for k, v in masked_counts.items()) or "  (없음)"
    content = (
        f"처리 결과 요약\n"
        f"================\n"
        f"원본 파일명 : {record['원본파일명']}\n"
        f"처리일시    : {record['처리일시']}\n"
        f"처리자      : {record['처리자']}\n"
        f"결과물 파일명: {record['결과물파일명']}\n"
        f"마스킹 유형별 건수:\n{counts_lines}\n"
    )
    report_path.write_text(content, encoding="utf-8")
    return report_path


def now_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today_date_str() -> str:
    return datetime.now().strftime("%Y%m%d")
