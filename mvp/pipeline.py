"""
처리 원자성 (6.5.2) + 원본 자동 이동 + 최소 로그 (6.6, 6.7) 오케스트레이션.

"마스킹본 저장 -> 자체 재검증 -> 원본 이동 -> 로그 기록"을 하나의 트랜잭션으로 취급한다.
마스킹/재검증은 워크스페이스 밖 임시 경로에서 먼저 끝내고, 재검증을 통과한 뒤에만
원본 이동 + 최종 위치 배치 + 로그 기록을 시도한다. 이 중 어느 하나라도 실패하면
이미 반영된 것들을 되돌려 원본은 원래 위치 그대로 남긴다 (일부만 처리된 상태 금지).

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from audit_log import append_entry, sha256_file
from detector import Finding
from masker import mask_pdf

ORIGINAL_DIR_NAME = "원본_보관"
MASKED_DIR_NAME = "마스킹완료"
LOG_DIR_NAME = "로그"
LOG_FILE_NAME = "audit_log.csv"


@dataclass
class PipelineResult:
    success: bool
    output_path: str | None = None
    original_moved_to: str | None = None
    masked_counts: dict[str, int] = field(default_factory=dict)
    leftover: list[str] = field(default_factory=list)
    rotated_text_warning: bool = False
    hidden_content_warning: bool = False
    error: str | None = None  # "self_check_failed" | "commit_failed: <ExcType>" | None


def _unique_dest(dest_dir: Path, filename: str) -> Path:
    """대상 폴더에 동명 파일이 있으면 덮어쓰지 않도록 번호를 붙인다."""
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 1
    while True:
        candidate = dest_dir / f"{stem}({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def process_atomic(
    input_path: str,
    findings: list[Finding],
    workspace_dir: str,
    processor: str | None = None,
) -> PipelineResult:
    """탐지->검토 완료된 findings로 마스킹부터 로그 기록까지를 원자적으로 수행.

    성공 시에만: 마스킹본이 마스킹완료/에 놓이고, 원본이 원본_보관/으로 이동하고, 로그 1건 기록.
    실패 시: 위 세 가지 중 어느 것도 반영되지 않고 원본은 원래 위치에 그대로 남는다.
    """
    src = Path(input_path)
    workspace = Path(workspace_dir)
    masked_dir = workspace / MASKED_DIR_NAME
    original_dir = workspace / ORIGINAL_DIR_NAME
    log_path = workspace / LOG_DIR_NAME / LOG_FILE_NAME

    # ⚠ 이전 버전은 이 mkdir이 트랜잭션 try 블록 밖에 있어서, 워크스페이스 사전조건이
    # 깨진 경우(예: 원본_보관 자리에 일반 파일이 이미 있음) 예외가 그대로 호출자(main.py)
    # 까지 전파돼 사용자에게 파이썬 트레이스백이 노출됐다 (실측으로 확인). 원본은 아직
    # 손대지 않은 시점이라 안전하지만, 깔끔한 실패 결과로 감싸도록 수정.
    try:
        masked_dir.mkdir(parents=True, exist_ok=True)
        original_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return PipelineResult(False, error=f"workspace_setup_failed: {type(exc).__name__}")

    # 1. 마스킹 + 자체 재검증은 워크스페이스 밖 임시 경로에서 먼저 수행 (아직 아무것도 확정 아님)
    tmp_dir = tempfile.mkdtemp(prefix="masking_tmp_")
    tmp_masked_path = os.path.join(tmp_dir, src.stem + "_masked.pdf")
    try:
        result = mask_pdf(str(src), findings, tmp_masked_path)

        if not result.success:
            # 자체 재검증 실패: 원본/마스킹완료/로그 모두 손대지 않음 (6.5.1)
            return PipelineResult(
                False, leftover=result.leftover,
                rotated_text_warning=result.rotated_text_warning,
                hidden_content_warning=result.hidden_content_warning,
                error="self_check_failed",
            )

        # 2. 여기서부터 원본 이동 + 최종 배치 + 로그 기록을 한 트랜잭션으로 커밋
        original_dest = _unique_dest(original_dir, src.name)
        masked_dest = _unique_dest(masked_dir, Path(tmp_masked_path).name)

        moved_original = False
        placed_masked = False
        try:
            original_sha256 = sha256_file(src)

            shutil.move(str(src), str(original_dest))
            moved_original = True

            shutil.move(tmp_masked_path, str(masked_dest))
            placed_masked = True

            append_entry(
                log_path,
                original_filename=src.name,
                original_sha256=original_sha256,
                output_filename=masked_dest.name,
                masked_counts=result.masked_counts,
                rotated_text_warning=result.rotated_text_warning,
                hidden_content_warning=result.hidden_content_warning,
                processor=processor,
            )
        except Exception as exc:
            # 트랜잭션 실패 -> 이미 반영된 부분을 되돌려 원본은 원래 위치로 복구
            if placed_masked and masked_dest.exists():
                os.remove(masked_dest)
            if moved_original and original_dest.exists():
                shutil.move(str(original_dest), str(src))
            return PipelineResult(False, error=f"commit_failed: {type(exc).__name__}")

        return PipelineResult(
            True,
            output_path=str(masked_dest),
            original_moved_to=str(original_dest),
            masked_counts=result.masked_counts,
            rotated_text_warning=result.rotated_text_warning,
            hidden_content_warning=result.hidden_content_warning,
        )
    finally:
        # 남은 임시 산출물은 성공/실패 불문 정리 (6.5.2)
        shutil.rmtree(tmp_dir, ignore_errors=True)
