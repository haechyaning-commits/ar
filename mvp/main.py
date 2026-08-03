"""
MVP 통합 진입점.

범위(설계서 기준 MVP): PDF 입력만 지원(hwp/hwpx 변환 어댑터는 제외),
탐지 -> 최소 검토(전체승인+예외해제, 문서유형 선택) -> 실제 마스킹+자체검증
-> 파일명 규칙에 맞춰 저장(6.6) -> 원본 보관 이동(6.6) -> 로그/요약리포트 기록(6.7, 6.8).

재실행(중복 마스킹) 방지(6.4)는 MVP 범위 밖으로 남겨둠 (설계서 "제외 (나중에)" 항목).

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF

from archive import archive_original, build_output_path
from detector import detect_all
from logger import record_processing
from masker import mask_pdf
from review_ui import run_review


def process_file(input_path: str, output_path: str | None = None,
                  _auto_approve_after_ms: int | None = None) -> int:
    """반환값: 0=성공, 1=탐지 없음(그대로 종료), 2=검토 취소, 3=자체검증 실패

    output_path를 직접 지정하면 6.6 파일명 규칙([문서유형]_[일자]_[일련번호])을
    건너뛰고 그 경로에 그대로 저장 -- 테스트/디버그용 탈출구.
    """
    src = Path(input_path)

    doc = fitz.open(input_path)
    full_text = "\n".join(page.get_text(sort=True) for page in doc)
    doc.close()

    findings = detect_all(full_text)
    if not findings:
        print(f"[{src.name}] 탐지된 개인정보가 없습니다.")
        return 1

    print(f"[{src.name}] {len(findings)}건 탐지됨 -> 검토 화면 표시")
    reviewed = run_review(src.name, findings, _auto_approve_after_ms)
    if reviewed is None:
        print(f"[{src.name}] 검토가 취소되었습니다. 처리하지 않음.")
        return 2
    findings, doc_type = reviewed

    if output_path is None:
        output_path = str(build_output_path(src.parent, doc_type))

    result = mask_pdf(input_path, findings, output_path)

    if not result.success:
        print(f"[{src.name}] 자체 재검증 실패 — 저장하지 않음. 남은 항목: {result.leftover}")
        return 3

    # 6.5.2 순서: 마스킹본 저장(mask_pdf 안에서 완료) -> 원본 이동 -> 로그 기록
    original_archived_at = archive_original(input_path, src.parent)
    log_path, report_path = record_processing(
        str(original_archived_at), result.output_path, result.masked_counts, base_dir=src.parent,
    )
    print(f"[{src.name}] 마스킹 완료 -> {result.output_path}")
    print(f"  유형별 건수: {result.masked_counts}")
    print(f"  원본 보관 위치: {original_archived_at}")
    print(f"  로그: {log_path} / 요약리포트: {report_path}")
    if result.rotated_text_warning:
        print("  ⚠ 회전된 텍스트가 있습니다. 자동 탐지가 놓쳤을 수 있으니 육안으로 재확인하세요.")
    if result.hidden_content_warning:
        print("  ⚠ 주석/폼필드/첨부파일 등 숨겨진 콘텐츠가 있습니다. 별도 확인이 필요합니다.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 main.py <입력.pdf> [출력.pdf]")
        sys.exit(1)
    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(process_file(in_path, out_path))
