"""
감사 로그 + 처리 결과 요약 리포트 (6.7, 6.8).

- 로그: 건수/위치 아닌 유형/건수만 기록, 실제 개인정보 값은 남기지 않음 (6.7)
- 요약 리포트: 파일 1건당 사람이 읽기 좋은 1장짜리 텍스트 (6.8)
- 탐지출처(자동/수동, 6.3.1) 구분 표기 (6.7 신규)

기존 로그를 일/주/월 단위로 집계하는 정확도 신뢰 리포트(6.7.1)는 이 로그가
어느 정도 쌓인 뒤에 의미가 있는 별도 기능이라 이번 범위 밖. 원본 파일의
원본_보관/ 이동, 결과물 파일명 규칙([문서유형]_[일자]_[일련번호])은 6.6에
속하는 별개 기능이라 이 모듈 범위 밖.
"""
from __future__ import annotations

import csv
import getpass
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LOG_DIR_NAME = "로그"
REPORT_DIR_NAME = "요약리포트"
LOG_FILENAME = "masking_log.csv"
LOG_HEADER = ["처리일시", "원본파일명", "원본_SHA256", "유형별건수", "결과물파일명", "처리자"]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_processor() -> str:
    """처리자 식별 (6.7): Windows 로그인 계정명을 기본으로 사용."""
    return getpass.getuser()


def _format_counts(counts: dict[str, dict[str, int]]) -> str:
    # 설계서 6.7 예시 형식: "이름 2건(자동 1, 수동 1), 전화번호 1건(자동 1)"
    parts = []
    for type_, by_source in counts.items():
        total = sum(by_source.values())
        detail = ", ".join(f"{source} {n}" for source, n in by_source.items())
        parts.append(f"{type_} {total}건({detail})")
    return ", ".join(parts)


@dataclass
class ProcessingRecord:
    timestamp: datetime
    original_path: str
    original_hash: str
    output_path: str
    counts: dict[str, dict[str, int]]
    processor: str


def write_log(base_dir: Path, record: ProcessingRecord) -> Path:
    log_dir = base_dir / LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILENAME

    is_new = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(LOG_HEADER)
        writer.writerow([
            record.timestamp.strftime("%Y-%m-%d %H:%M"),
            Path(record.original_path).name,
            record.original_hash,
            _format_counts(record.counts),
            Path(record.output_path).name,
            record.processor,
        ])
    return log_path


def write_summary_report(base_dir: Path, record: ProcessingRecord) -> Path:
    report_dir = base_dir / REPORT_DIR_NAME
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{Path(record.output_path).stem}_요약.txt"

    lines = [
        "마스킹 처리 결과 요약",
        "=" * 30,
        f"원본 파일명: {Path(record.original_path).name}",
        f"처리일시: {record.timestamp.strftime('%Y-%m-%d %H:%M')}",
        f"처리자: {record.processor}",
        f"마스킹 유형별 건수: {_format_counts(record.counts)}",
        f"결과물 파일명: {Path(record.output_path).name}",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def record_processing(
    input_path: str, output_path: str, counts: dict[str, dict[str, int]],
    base_dir: Path | str = ".",
) -> tuple[Path, Path]:
    """마스킹 성공 후 호출. 로그 1행 추가 + 요약 리포트 1개 생성."""
    base = Path(base_dir)
    record = ProcessingRecord(
        timestamp=datetime.now(),
        original_path=input_path,
        original_hash=sha256_file(input_path),
        output_path=output_path,
        counts=counts,
        processor=get_processor(),
    )
    log_path = write_log(base, record)
    report_path = write_summary_report(base, record)
    return log_path, report_path
