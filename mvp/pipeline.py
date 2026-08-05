"""
처리 원자성 (6.5.2) + 원본 자동 이동 + 최소 로그 (6.6, 6.7) + 요약 리포트 (6.8)
+ 재실행 방지 마커 기록 (6.4) 오케스트레이션.

"마스킹본 저장 -> 자체 재검증 -> 재실행 방지 마커 기록 -> 원본 이동 -> 로그 기록
-> 요약 리포트"를 하나의 트랜잭션으로 취급한다. 마스킹/재검증은 워크스페이스 밖
임시 경로에서 먼저 끝내고, 재검증을 통과한 뒤에만 나머지를 시도한다. 이 중 어느
하나라도 실패하면 이미 반영된 것들을 되돌려 원본은 원래 위치 그대로 남긴다
(일부만 처리된 상태 금지).

재실행 여부 확인(6.4, 이미 처리된 파일인지 검토자에게 물어보는 확인창)과 처리자
이름 확인(6.7)은 탐지/검토보다 먼저 필요한 사용자 상호작용이라 이 모듈이 아니라
main.py에서 처리한다 -- output.is_already_processed()/get_saved_processor_name()
등을 직접 사용.

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
from output import (
    DOCUMENT_TYPES, build_output_filename, ensure_folders,
    mark_processed_file, next_serial, now_timestamp, today_date_str, write_summary_report,
)

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
    doc_type: str = DOCUMENT_TYPES[0],
    review_seconds: float = 0.0,
) -> PipelineResult:
    """탐지->검토 완료된 findings로 마스킹부터 요약 리포트까지를 원자적으로 수행.

    성공 시에만: 마스킹본이 마스킹완료/에 [문서유형]_[처리일자]_[일련번호].pdf로 놓이고
    (6.6), 재실행 방지 마커가 기록되고(6.4), 원본이 원본_보관/으로 이동하고, 로그 1건과
    요약 리포트 1건이 기록된다(6.7, 6.8).
    실패 시: 위 어느 것도 반영되지 않고 원본은 원래 위치에 그대로 남는다.
    """
    src = Path(input_path)
    workspace = Path(workspace_dir)

    # ⚠ 이전 버전은 이 mkdir이 트랜잭션 try 블록 밖에 있어서, 워크스페이스 사전조건이
    # 깨진 경우(예: 원본_보관 자리에 일반 파일이 이미 있음) 예외가 그대로 호출자(main.py)
    # 까지 전파돼 사용자에게 파이썬 트레이스백이 노출됐다 (실측으로 확인). 원본은 아직
    # 손대지 않은 시점이라 안전하지만, 깔끔한 실패 결과로 감싸도록 수정.
    try:
        folders = ensure_folders(workspace)
    except OSError as exc:
        return PipelineResult(False, error=f"workspace_setup_failed: {type(exc).__name__}")
    log_path = folders.logs / LOG_FILE_NAME

    # 1. 마스킹 + 자체 재검증은 워크스페이스 밖 임시 경로에서 먼저 수행 (아직 아무것도 확정 아님)
    tmp_dir = tempfile.mkdtemp(prefix="masking_tmp_")
    tmp_masked_path = os.path.join(tmp_dir, src.stem + "_masked.pdf")
    try:
        result = mask_pdf(str(src), findings, tmp_masked_path)

        if not result.success:
            # 자체 재검증 실패: 원본/마스킹완료/로그/리포트 모두 손대지 않음 (6.5.1)
            return PipelineResult(
                False, leftover=result.leftover,
                rotated_text_warning=result.rotated_text_warning,
                hidden_content_warning=result.hidden_content_warning,
                error="self_check_failed",
            )

        # 2. 여기서부터 마커 기록 + 원본 이동 + 최종 배치 + 로그/리포트 기록을 한
        # 트랜잭션으로 커밋. 파일명은 설계서 6.6 규칙([문서유형]_[처리일자]_[일련번호])을
        # 따름 -- 원본파일명을 그대로 쓰면 파일명 자체에 개인정보가 남을 수 있음(v8).
        date_str = today_date_str()
        serial = next_serial(folders.masked, date_str)
        masked_dest = folders.masked / build_output_filename(doc_type, date_str, serial)
        original_dest = _unique_dest(folders.originals, src.name)

        moved_original = False
        placed_masked = False
        try:
            original_sha256 = sha256_file(src)

            shutil.move(tmp_masked_path, str(masked_dest))
            placed_masked = True

            # 6.4: 재실행 방지 마커는 최종 위치에 놓인 뒤(=더 이상 임시 경로가 아닌
            # 상태에서) 기록 -- 마커 기록 자체가 실패해도 아래 except가 masked_dest를
            # 정리하므로 "마커 없는 마스킹본"이 남는 일은 없음
            mark_processed_file(masked_dest)

            shutil.move(str(src), str(original_dest))
            moved_original = True

            append_entry(
                log_path,
                original_filename=src.name,
                original_sha256=original_sha256,
                output_filename=masked_dest.name,
                masked_counts=result.masked_counts,
                rotated_text_warning=result.rotated_text_warning,
                hidden_content_warning=result.hidden_content_warning,
                processor=processor,
                review_seconds=review_seconds,
                source_breakdown=result.source_breakdown,
            )
            write_summary_report(
                folders.reports,
                {
                    "처리일시": now_timestamp(),
                    "원본파일명": original_dest.name,
                    "처리자": processor or "",
                    "결과물파일명": masked_dest.name,
                },
                result.masked_counts,
                rotated_text_warning=result.rotated_text_warning,
                hidden_content_warning=result.hidden_content_warning,
                source_breakdown=result.source_breakdown,
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
