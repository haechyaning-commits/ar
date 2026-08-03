"""
MVP 통합 진입점.

범위(설계서 기준 MVP): PDF 입력만 지원(hwp/hwpx 변환 어댑터는 제외),
탐지 -> 최소 검토(전체승인+예외해제) -> 실제 마스킹+자체검증 -> 원본 자동 이동
+ 최소 로그 기록까지를 하나의 원자적 트랜잭션으로 처리 (6.5.2, 6.6, 6.7).

요약리포트/재실행 방지/파일명 규칙([문서유형]_[날짜]_[일련번호]) 등은
MVP 범위 밖으로 남겨둠 (설계서 "제외 (나중에)" 항목).

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF

from detector import detect_all
from pdf_extract import extract_text_and_spans
from pipeline import process_atomic
from review_ui import run_review


def process_file(input_path: str, workspace_dir: str | None = None,
                  _auto_approve_after_ms: int | None = None) -> int:
    """반환값: 0=성공, 1=탐지 없음(그대로 종료), 2=검토 취소,
    3=자체검증 실패(원본 무변경), 4=원본이동/로그 커밋 실패(원본 무변경, 롤백됨)"""
    src = Path(input_path)
    workspace = Path(workspace_dir) if workspace_dir else src.parent

    doc = fitz.open(input_path)
    # masker.py도 같은 방식(pdf_extract)으로 다시 추출해 findings의 start/end를
    # 실제 좌표로 되찾으므로, 탐지 쪽도 반드시 이 함수를 써야 오프셋이 어긋나지 않음
    full_text, spans = extract_text_and_spans(doc)
    total_pages = doc.page_count
    doc.close()

    findings = detect_all(full_text)
    if not findings:
        print(f"[{src.name}] 탐지된 개인정보가 없습니다.")
        return 1

    print(f"[{src.name}] {len(findings)}건 탐지됨 -> 검토 화면 표시")
    reviewed = run_review(src.name, findings, full_text, spans, total_pages, input_path,
                           _auto_approve_after_ms=_auto_approve_after_ms)
    if reviewed is None:
        print(f"[{src.name}] 검토가 취소되었습니다. 처리하지 않음.")
        return 2

    result = process_atomic(str(src), reviewed, str(workspace))

    if not result.success:
        if result.error == "self_check_failed":
            print(f"[{src.name}] 자체 재검증 실패 — 저장하지 않음. 남은 항목 수: {len(result.leftover)}")
            return 3
        print(f"[{src.name}] 원본 이동/로그 기록 실패 — 원본은 원래 위치로 되돌렸습니다. ({result.error})")
        return 4

    print(f"[{src.name}] 마스킹 완료 -> {result.output_path}")
    print(f"  원본 이동 -> {result.original_moved_to}")
    print(f"  유형별 건수: {result.masked_counts}")
    if result.rotated_text_warning:
        print("  ⚠ 회전된 텍스트가 있습니다. 자동 탐지가 놓쳤을 수 있으니 육안으로 재확인하세요.")
    if result.hidden_content_warning:
        print("  ⚠ 주석/폼필드/첨부파일 등 숨겨진 콘텐츠가 있습니다. 별도 확인이 필요합니다.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 main.py <입력.pdf> [작업폴더]")
        print("  작업폴더 생략 시 입력 파일과 같은 폴더에 원본_보관/ 마스킹완료/ 로그/ 를 생성합니다.")
        sys.exit(1)
    in_path = sys.argv[1]
    ws_dir = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(process_file(in_path, ws_dir))
