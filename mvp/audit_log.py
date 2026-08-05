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
    "output_filename", "masked_counts", "detection_source", "rotated_text_warning",
    "hidden_content_warning", "review_seconds",
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


# 자동탐지가 수동추가보다 먼저 오도록 고정 순서 (6.7 탐지 출처 구분, v13 신규)
_SOURCE_ORDER = {"자동": 0, "수동": 1}


def format_source_breakdown(breakdown: dict[str, dict[str, int]]) -> str:
    """유형별 탐지 출처 세부 내역을 사람이 읽기 좋은 문자열로 변환 (6.7).

    예: "이름 2건(자동 1, 수동 1), 전화번호 1건(자동)". 출처가 하나뿐이면
    총 건수와 중복이라 굳이 숫자를 다시 적지 않고 출처 이름만 표시한다.
    """
    parts = []
    for type_name in sorted(breakdown.keys()):
        by_source = breakdown[type_name]
        total = sum(by_source.values())
        ordered = sorted(by_source.items(), key=lambda kv: _SOURCE_ORDER.get(kv[0], 99))
        if len(ordered) == 1:
            detail = ordered[0][0]
        else:
            detail = ", ".join(f"{src} {cnt}" for src, cnt in ordered)
        parts.append(f"{type_name} {total}건({detail})")
    return ", ".join(parts)


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
    source_breakdown: dict[str, dict[str, int]] | None = None,
) -> None:
    """로그 한 줄을 append. 개인정보 실값(이름/번호 등)은 남기지 않는다 (6.5.3, 6.7).

    review_seconds(실사용자 피드백, 아이디어 9): review_ui.ReviewWindow가 검토창이
    열린 시점부터 승인까지 걸린 시간(초)을 재서 넘겨줌 -- 개인정보가 아니라
    안전하게 기록 가능. 12.6이 "실사용 전 남은 진짜 미검증"으로 남겨뒀던
    "사람이 검토에 실제로 걸리는 시간"을 실측할 근거 데이터가 시간이 지나며
    쌓이게 하기 위함(헤드리스 자동승인 시에는 그 딜레이 값이 그대로 찍히므로
    실사용 통계를 볼 때는 처리자/일자 기준으로 걸러서 봐야 함).

    source_breakdown(6.7 탐지 출처 구분, v13 신규): masker.mask_pdf가 유형별로
    자동탐지/수동추가 건수를 이미 나눠서 넘겨주므로, 여기서는 사람이 읽기 좋은
    문자열로만 변환해 별도 컬럼에 남긴다 -- 어떤 문서유형에서 자동탐지가 유독
    많이 놓치는지 나중에 6.7.1/11번 통계로 발전시킬 근거 데이터가 쌓이게 함."""
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
        "detection_source": format_source_breakdown(source_breakdown or {}),
        "rotated_text_warning": rotated_text_warning,
        "hidden_content_warning": hidden_content_warning,
        "review_seconds": round(review_seconds, 1),
    }

    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
