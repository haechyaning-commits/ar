"""
결과물 파일명 규칙 + 원본 자동 보관 (6.6).

- 결과물 파일명: [문서유형]_[처리일자]_[일련번호].pdf
  원본파일명을 그대로 쓰면 파일명 자체에 개인정보가 남을 수 있어(v8) 이 형식으로 통일.
  문서유형은 자동판정하지 않고 검토 화면(6.3)에서 검토자가 직접 고른 값을 사용.
- 원본: 처리 완료 후 삭제하지 않고 원본_보관/ 폴더로 이동 (매핑은 로그 6.7 참고)

- is_in_masked_dir(): 재실행(중복 마스킹) 방지(6.4)의 감지 방법 ① -- 입력 파일이
  마스킹완료/ 폴더 경로 안에 있으면(=이미 만들어진 결과물을 다시 넣은 경우) 감지.
  감지 방법 ②(문서 속성 마커)는 masker.has_processed_marker() 담당, 확인 절차는
  main.py 담당.
"""
from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

MASKED_DIR_NAME = "마스킹완료"
ORIGINAL_DIR_NAME = "원본_보관"

_SERIAL_RE_TEMPLATE = r"^{doc_type}_{date_str}_(\d+)\.pdf$"

# 직접 입력한 문서유형이 그대로 파일 경로 일부가 되므로(6.3), 경로 구분자는
# 제거하고(안 그러면 의도치 않은 하위 폴더가 생김) 나머지 공백/특수문자는
# 언더스코어로 치환.
_UNSAFE_CHARS_RE = re.compile(r"[\\/:*?\"<>|\s]+")


def sanitize_doc_type(doc_type: str) -> str:
    cleaned = _UNSAFE_CHARS_RE.sub("_", doc_type.strip()).strip("_")
    return cleaned or "기타"


def _next_serial(masked_dir: Path, doc_type: str, date_str: str) -> int:
    if not masked_dir.exists():
        return 1
    pattern = re.compile(_SERIAL_RE_TEMPLATE.format(
        doc_type=re.escape(doc_type), date_str=re.escape(date_str),
    ))
    existing = [
        int(m.group(1)) for p in masked_dir.iterdir()
        if (m := pattern.match(p.name))
    ]
    return max(existing, default=0) + 1


def build_output_path(base_dir: Path, doc_type: str, today: date | None = None) -> Path:
    """오늘 날짜 + 문서유형 기준 다음 일련번호로 결과물 경로를 만듦.
    마스킹완료/ 폴더가 없으면 만듦 (실제 파일 저장은 호출하는 쪽이 담당)."""
    masked_dir = base_dir / MASKED_DIR_NAME
    masked_dir.mkdir(parents=True, exist_ok=True)

    doc_type = sanitize_doc_type(doc_type)
    date_str = (today or date.today()).strftime("%Y%m%d")
    serial = _next_serial(masked_dir, doc_type, date_str)
    return masked_dir / f"{doc_type}_{date_str}_{serial:03d}.pdf"


def _unique_destination(directory: Path, filename: str) -> Path:
    """같은 이름의 원본이 이미 보관돼 있으면 덮어쓰지 않고 번호를 붙여 피함
    (버그: 파일명이 겹치는 서로 다른 원본을 처리하면 앞서 보관한 원본이
    shutil.move에 덮어써져 사라지는 문제가 있었음 -- "삭제하지 않음" 원칙 위반)."""
    dest = directory / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 1
    while (dest := directory / f"{stem}_{n}{suffix}").exists():
        n += 1
    return dest


def archive_original(input_path: str, base_dir: Path) -> Path:
    """원본을 원본_보관/으로 이동 (삭제하지 않음). 이동 후 경로를 반환."""
    original_dir = base_dir / ORIGINAL_DIR_NAME
    original_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_destination(original_dir, Path(input_path).name)
    shutil.move(input_path, dest)
    return dest


def is_in_masked_dir(path: str | Path) -> bool:
    """6.4 감지 방법 ①: 파일명 규칙(v8부터 [문서유형]_[일자]_[일련번호])은
    더 이상 신뢰할 수 없어, 경로 자체가 마스킹완료/ 폴더 안인지로 판별."""
    return MASKED_DIR_NAME in Path(path).parts
