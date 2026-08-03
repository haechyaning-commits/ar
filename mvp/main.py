"""
MVP 통합 진입점.

범위(설계서 기준 MVP): PDF 입력만 지원(hwp/hwpx 변환 어댑터는 제외), 재실행(중복
마스킹) 방지(6.4) -> 탐지 -> 최소 검토(전체승인+예외해제, 문서유형 선택, 수동추가)
-> 실제 마스킹+자체검증(실패 시 검토 화면 복귀, 6.5.1) -> 파일명 규칙에 맞춰
저장(6.6) -> 원본 보관 이동(6.6) -> 로그/요약리포트 기록(6.7, 6.8). 여러 파일은
process_batch()로 대기열 순차 처리(7번 정책표) -- 배치 자동처리가 아니라 파일마다
검토 화면을 거침.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import fitz  # PyMuPDF

from archive import archive_original, build_output_path, is_in_masked_dir
from detector import detect_all
from logger import record_processing
from masker import has_processed_marker, mask_pdf
from review_ui import confirm_reprocess, run_review


def process_file(input_path: str, output_path: str | None = None,
                  _auto_approve_after_ms: int | None = None,
                  _auto_confirm_reprocess: bool | None = None) -> int:
    """반환값: 0=성공, 1=탐지 없음(그대로 종료), 2=검토 취소,
    3=자체검증 실패(헤드리스 모드에서만 -- 대화형에서는 검토 화면으로 복귀함),
    4=원본 보관/로그 기록 실패(마스킹본·원본 롤백됨), 5=재처리를 확인 후 취소함(6.4)

    output_path를 직접 지정하면 6.6 파일명 규칙([문서유형]_[일자]_[일련번호])을
    건너뛰고 그 경로에 그대로 저장 -- 테스트/디버그용 탈출구.
    """
    src = Path(input_path)
    explicit_output_path = output_path

    doc = fitz.open(input_path)
    full_text = "\n".join(page.get_text(sort=True) for page in doc)
    already_processed_reasons = []
    if is_in_masked_dir(input_path):
        already_processed_reasons.append("마스킹완료/ 폴더 안에 있는 파일입니다 (이미 만들어진 결과물일 수 있음)")
    if has_processed_marker(doc):
        already_processed_reasons.append("문서 속성에 이 도구의 처리완료 표시가 남아있습니다")
    doc.close()

    if already_processed_reasons:
        # 6.4: 완전 차단이 아니라 확인만 거치면 재처리 허용
        if not confirm_reprocess(src.name, already_processed_reasons, _auto_confirm_reprocess):
            print(f"[{src.name}] 이미 처리된 파일로 보여 재처리를 취소했습니다.")
            return 5

    findings = detect_all(full_text)
    if not findings:
        print(f"[{src.name}] 탐지된 개인정보가 없습니다.")
        return 1

    print(f"[{src.name}] {len(findings)}건 탐지됨 -> 검토 화면 표시")
    retry_notice: str | None = None
    highlight_spans: set[tuple[int, int]] = set()
    last_doc_type: str | None = None

    while True:
        reviewed = run_review(
            src.name, findings, full_text, _auto_approve_after_ms,
            retry_notice=retry_notice, highlight_spans=highlight_spans,
            initial_doc_type=last_doc_type,
        )
        if reviewed is None:
            print(f"[{src.name}] 검토가 취소되었습니다. 처리하지 않음.")
            return 2
        findings, doc_type = reviewed
        last_doc_type = doc_type

        current_output_path = explicit_output_path or str(build_output_path(src.parent, doc_type))
        result = mask_pdf(input_path, findings, current_output_path)
        if result.success:
            break

        # 6.5.1: 재검증 실패 -> 저장 차단. 실제 값은 어디에도 노출하지 않고(6.5.3)
        # 건수만 안내한 뒤, 검토 화면으로 복귀해 해당 항목을 하이라이트(highlight_spans)
        print(f"[{src.name}] 자체 재검증 실패 — 저장하지 않음. {len(result.leftover)}건이 완전히 지워지지 않았습니다.")
        if _auto_approve_after_ms is not None:
            # 헤드리스(자동승인) 모드는 사람이 없어 재시도 루프를 돌 수 없음 -> 즉시 실패 반환
            return 3
        leftover_values = set(result.leftover)
        highlight_spans = {(f.start, f.end) for f in findings if f.value in leftover_values}
        retry_notice = (
            f"이전 시도에서 {len(result.leftover)}건이 완전히 지워지지 않았습니다. "
            "아래 ⚠[미제거] 표시된 항목을 확인 후 다시 승인해 재시도하거나, "
            "구조적 문제로 계속 실패하면 해당 항목 유형/페이지를 개발자에게 보고하세요."
        )

    # 6.5.2 순서: 마스킹본 저장(mask_pdf 안에서 완료) -> 원본 이동 -> 로그 기록.
    # 이 구간에서 실패하면 "마스킹본은 생겼는데 원본 이동/로그는 안 된" 애매한
    # 상태가 남지 않도록 롤백 (6.5.2 "하나의 단위로 취급" 요구사항)
    original_archived_at = None
    try:
        original_archived_at = archive_original(input_path, src.parent)
        log_path, report_path = record_processing(
            str(original_archived_at), result.output_path, result.masked_counts, base_dir=src.parent,
        )
    except Exception as exc:
        if original_archived_at is not None and original_archived_at.exists():
            shutil.move(str(original_archived_at), input_path)
        Path(result.output_path).unlink(missing_ok=True)
        print(f"[{src.name}] 원본 보관/로그 기록 중 오류가 발생해 처리를 롤백했습니다: {type(exc).__name__}")
        return 4

    print(f"[{src.name}] 마스킹 완료 -> {result.output_path}")
    print(f"  유형별 건수: {result.masked_counts}")
    print(f"  원본 보관 위치: {original_archived_at}")
    print(f"  로그: {log_path} / 요약리포트: {report_path}")
    if result.rotated_text_warning:
        print("  ⚠ 회전된 텍스트가 있습니다. 자동 탐지가 놓쳤을 수 있으니 육안으로 재확인하세요.")
    hc = result.hidden_content
    if hc.removed_annotations or hc.removed_widgets or hc.removed_attachments:
        print(
            f"  숨겨진 콘텐츠 제거됨: 주석 {hc.removed_annotations}건, "
            f"폼필드 {hc.removed_widgets}건, 첨부파일 {hc.removed_attachments}건"
        )
    if hc.ocg_present:
        print("  ⚠ 선택적 콘텐츠 레이어(OCG)가 있습니다. 내용 스캔이 불가능하니 육안으로 확인하세요.")
    return 0


def process_batch(
    input_paths: list[str], _auto_approve_after_ms: int | None = None,
) -> dict[str, int]:
    """여러 파일을 대기열로 순차 처리 (7번 정책표).

    배치 자동 처리가 아니라 파일마다 사람이 검토 화면에서 확인하는 구조 --
    이 함수는 process_file()을 파일 개수만큼 반복 호출할 뿐, 검토를 건너뛰지
    않음. 한 파일이 실패/취소돼도 나머지 파일 처리는 계속 진행 (hwp 변환의
    "실패한 파일만 대기열에서 빼고 배치는 안 멈춤" 원칙과 같은 방향).

    반환값: {입력 경로: process_file() 반환코드} 딕셔너리.
    """
    results: dict[str, int] = {}
    total = len(input_paths)
    for i, input_path in enumerate(input_paths, start=1):
        print(f"\n=== [{i}/{total}] {Path(input_path).name} 처리 시작 ===")
        results[input_path] = process_file(input_path, _auto_approve_after_ms=_auto_approve_after_ms)

    succeeded = sum(1 for rc in results.values() if rc == 0)
    print(f"\n=== 배치 처리 완료: {succeeded}/{total}건 마스킹 완료 ===")
    for path, rc in results.items():
        if rc != 0:
            reason = {
                1: "탐지된 개인정보 없음", 2: "검토 취소", 3: "자체검증 실패",
                4: "롤백됨", 5: "재처리 확인 후 취소됨",
            }.get(rc, f"코드 {rc}")
            print(f"  - {Path(path).name}: {reason}")
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 main.py <입력1.pdf> [입력2.pdf ...]")
        sys.exit(1)
    batch_results = process_batch(sys.argv[1:])
    sys.exit(1 if any(rc == 4 for rc in batch_results.values()) else 0)
