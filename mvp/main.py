"""
MVP 통합 진입점.

범위(설계서 기준 MVP): PDF 입력만 지원(hwp/hwpx 변환 어댑터는 제외, Windows+한컴오피스
환경 필요해 이 세션에서 검증 불가). 그 외 아키텍처(5번 섹션)의 핵심 흐름은 전부 구현:

탐지 -> 재실행 방지 확인(6.4) -> 검토(전체승인+예외해제+문서유형 선택, 6.3)
-> 실제 마스킹+자체검증(6.5) -> 처리완료 마커(6.4) -> 원본 보관(6.6)
-> 감사로그(6.7) -> 요약리포트(6.8), 실패 시 원본 무변경(6.5.2).

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from detector import detect_all
from masker import mask_pdf
from review_ui import run_review
import output


def process_file(input_path: str, output_path: str | None = None,
                  _auto_approve_after_ms: int | None = None,
                  _auto_processor_name: str | None = None,
                  _auto_reprocess_confirm: bool | None = None) -> int:
    """반환값:
    0=성공, 1=탐지 없음, 2=검토 취소, 3=자체검증 실패,
    4=재실행 확인에서 취소, 5=출력 처리(마커/이동/로그/리포트) 실패, 6=출력 폴더 준비 실패

    _auto_processor_name / _auto_reprocess_confirm: 실사용에서는 쓰지 않음.
    화면이 없는 환경(headless)에서 통합 테스트할 때, 사용자 입력을 기다리는
    다이얼로그(처리자 이름 입력/재실행 확인)를 자동 응답으로 대체하기 위한 테스트 전용 훅.
    """
    src = Path(input_path).resolve()
    app = QApplication.instance() or QApplication([])

    try:
        folders = output.ensure_folders(output.app_base_dir())
    except OSError as exc:
        print(f"[{src.name}] 출력 폴더 준비 실패 — 처리하지 않음. ({type(exc).__name__})")
        return 6

    if output.is_already_processed(src):
        if _auto_reprocess_confirm is not None:
            confirmed = _auto_reprocess_confirm
        else:
            reply = QMessageBox.question(
                None, "다시 처리하시겠습니까?",
                f"'{src.name}'은(는) 이미 마스킹 처리된 것으로 보입니다.\n다시 처리하시겠습니까?",
            )
            confirmed = reply == QMessageBox.StandardButton.Yes
        if not confirmed:
            print(f"[{src.name}] 이미 처리된 파일 — 재실행이 취소되었습니다.")
            return 4

    doc = fitz.open(str(src))
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

    if output_path is None:
        date_str = output.today_date_str()
        serial = output.next_serial(folders.masked, date_str)
        filename = output.build_output_filename(reviewed.doc_type, date_str, serial)
        final_output_path = folders.masked / filename
    else:
        final_output_path = Path(output_path)

    result = mask_pdf(str(src), reviewed.findings, str(final_output_path))

    if not result.success:
        print(f"[{src.name}] 자체 재검증 실패 — 저장하지 않음. 남은 항목 수: {len(result.leftover)}")
        return 3

    print(f"[{src.name}] 마스킹 완료 -> {result.output_path}")
    print(f"  유형별 건수: {result.masked_counts}")
    if result.rotated_text_warning:
        print("  ⚠ 회전된 텍스트가 있습니다. 자동 탐지가 놓쳤을 수 있으니 육안으로 재확인하세요.")
    if result.hidden_content_warning:
        print("  ⚠ 주석/폼필드/첨부파일 등 숨겨진 콘텐츠가 있습니다. 별도 확인이 필요합니다.")

    if output_path is not None:
        # 명시적으로 출력 경로를 지정한 호출(테스트 등)은 폴더 이동/로그/리포트 생략
        return 0

    try:
        output.mark_processed_file(final_output_path)

        processor = output.get_saved_processor_name(folders.logs)
        if processor is None:
            if _auto_processor_name is not None:
                processor = _auto_processor_name
            else:
                name, ok = QInputDialog.getText(
                    None, "처리자 확인",
                    "처리자 이름을 입력하세요 (미입력 시 이 PC 로그인 계정명 사용):",
                    text=output.default_processor_name(),
                )
                processor = name.strip() if ok and name.strip() else output.default_processor_name()
            output.save_processor_name(folders.logs, processor)

        original_hash = output.sha256_file(src)
        moved_original = output.move_original_to_storage(src, folders.originals)

        record = {
            "처리일시": output.now_timestamp(),
            "원본파일명": moved_original.name,
            "원본해시": original_hash,
            "마스킹건수요약": ", ".join(f"{k} {v}건" for k, v in result.masked_counts.items()),
            "결과물파일명": final_output_path.name,
            "처리자": processor,
        }
        output.append_audit_log(folders.logs, record)
        output.write_summary_report(folders.reports, record, result.masked_counts)
    except Exception as e:
        print(f"[{src.name}] 출력 후처리(마커/원본이동/로그/리포트) 중 오류: {e}")
        return 5

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 main.py <입력.pdf> [출력.pdf]")
        sys.exit(1)
    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(process_file(in_path, out_path))
