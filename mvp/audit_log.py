"""
최소 감사 로그 (6.7 MVP 버전).

건수/유형 + 원본 파일 SHA-256 해시만 기록 -- 개인정보 실값은 절대 남기지 않는다 (6.5.3).
로컬 CSV 파일에 처리 1건당 한 줄씩 append.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import csv
import getpass
import hashlib
from datetime import datetime
from pathlib import Path

LOG_FIELDS = [
    "timestamp", "processor", "original_filename", "original_sha256",
    "output_filename", "masked_counts", "rotated_text_warning", "hidden_content_warning",
    "review_seconds",
]


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def current_processor() -> str:
    """OS 로그인 계정명을 처리자로 사용 (6.7). 확인 불가 시 'unknown'."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def format_counts(counts: dict[str, int]) -> str:
    return ";".join(f"{k}:{v}" for k, v in sorted(counts.items()))


def append_entry(
    log_path: str | Path,
    *,
    original_filename: str,
    original_sha256: str,
    output_filename: str,
    masked_counts: dict[str, int],
    rotated_text_warning: bool,
    hidden_content_warning: bool,
    processor: str | None = None,
    review_seconds: float = 0.0,
) -> None:
    """로그 한 줄을 append. 개인정보 실값(이름/번호 등)은 남기지 않는다 (6.5.3, 6.7).

    review_seconds(실사용자 피드백, 아이디어 9): review_ui.ReviewWindow가 검토창이
    열린 시점부터 승인까지 걸린 시간(초)을 재서 넘겨줌 -- 개인정보가 아니라
    안전하게 기록 가능. 12.6이 "실사용 전 남은 진짜 미검증"으로 남겨뒀던
    "사람이 검토에 실제로 걸리는 시간"을 실측할 근거 데이터가 시간이 지나며
    쌓이게 하기 위함(헤드리스 자동승인 시에는 그 딜레이 값이 그대로 찍히므로
    실사용 통계를 볼 때는 처리자/일자 기준으로 걸러서 봐야 함)."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()

    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "processor": processor or current_processor(),
        "original_filename": original_filename,
        "original_sha256": original_sha256,
        "output_filename": output_filename,
        "masked_counts": format_counts(masked_counts),
        "rotated_text_warning": rotated_text_warning,
        "hidden_content_warning": hidden_content_warning,
        "review_seconds": round(review_seconds, 1),
    }

    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
