"""
MVP 통합 진입점.

범위(설계서 기준 MVP): PDF 입력만 지원(hwp/hwpx 변환 어댑터는 제외, Windows+한컴오피스
환경 필요해 이 세션에서 검증 불가). 그 외 아키텍처(5번 섹션)의 핵심 흐름은 전부 구현:

탐지 -> 재실행 방지 확인(6.4) -> 검토(전체승인+예외해제+문서유형 선택, 6.3)
-> 실제 마스킹+자체검증(6.5, 실패 시 검토 화면으로 복귀해 재시도 -- 8번 리스크
"실패 후 후속 흐름") -> 재실행 방지 마커/원본 보관(6.6)/감사로그(6.7)/요약리포트(6.8)
까지를 하나의 원자적 트랜잭션으로 처리 (6.5.2), 실패 시 원본 무변경.

여러 파일은 process_batch()로 대기열 순차 처리(7번 정책표) -- 배치 자동처리가
아니라 파일마다 검토 화면을 거침.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

import compare_view
import template_fingerprint
from detector import detect_all
from masker import hidden_content_pages, rotated_text_pages
from pdf_extract import extract_text_and_spans, page_sizes
from pipeline import process_atomic
from review_ui import run_review
import output


def process_file(input_path: str, workspace_dir: str | None = None,
                  _auto_approve_after_ms: int | None = None,
                  _auto_processor_name: str | None = None,
                  _auto_reprocess_confirm: bool | None = None,
                  _auto_open_comparison: bool | None = None) -> int:
    """반환값: 0=성공, 1=탐지 없음(그대로 종료), 2=검토 취소,
    3=자체검증 실패(헤드리스 모드에서만 -- 대화형에서는 검토 화면으로 복귀함),
    4=재실행 확인에서 취소, 5=원본이동/마커/로그/리포트 커밋 실패(원본 무변경, 롤백됨),
    6=출력 폴더 준비 실패

    workspace_dir: 생략하면 output.app_base_dir()(exe/스크립트 자신의 위치) 사용 --
    입력 파일이 어디 있든 결과가 항상 프로그램 옆 한 곳에 모이게 하기 위함(6.6).
    테스트에서만 임의 폴더를 명시적으로 지정.
    _auto_processor_name / _auto_reprocess_confirm: 실사용에서는 쓰지 않음.
    화면이 없는 환경(headless)에서 통합 테스트할 때, 사용자 입력을 기다리는
    다이얼로그(처리자 이름 입력/재실행 확인)를 자동 응답으로 대체하기 위한 테스트 전용 훅.
    _auto_open_comparison: 6.3.2 ② 비교 뷰를 열지 물어보는 QMessageBox를 헤드리스
    환경에서 대신 응답하는 테스트 전용 훅(다른 `_auto_*` 인자들과 동일한 관례) --
    지정하면 그 값을 답으로 쓰고 실제 대화상자는 띄우지 않음, 생략(None)하면
    실제로 사용자에게 물어봄.
    """
    src = Path(input_path).resolve()
    workspace = Path(workspace_dir) if workspace_dir else output.app_base_dir()
    app = QApplication.instance() or QApplication([])

    try:
        folders = output.ensure_folders(workspace)
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
    # masker.py도 같은 방식(pdf_extract)으로 다시 추출해 findings의 start/end를
    # 실제 좌표로 되찾으므로, 탐지 쪽도 반드시 이 함수를 써야 오프셋이 어긋나지 않음
    full_text, spans = extract_text_and_spans(doc)
    sizes = page_sizes(doc)
    total_pages = doc.page_count
    # 6.3.4 경고 페이지 색 강조: 실제 리댁션(masker.mask_pdf)이 하기 전, 검토
    # 화면을 띄우기 전에 미리 알아야 진행바에 표시할 수 있음 -- 마스킹 이후에나
    # 계산되던 rotated_text_warning/hidden_content_warning과 같은 판정 로직을
    # 여기서도 재사용(페이지 단위로).
    warning_pages = rotated_text_pages(doc) | hidden_content_pages(doc)
    doc.close()

    findings = detect_all(full_text)
    # 6.2.2: 저장된 템플릿 지문 중 이 문서와 양식이 일치하는 게 있으면, 탐지
    # 엔진이 이번엔 놓친 위치를 우선 확인 후보로 추가 (탐지가 0건이어도 템플릿
    # 지문만으로 후보가 나올 수 있음 -- 그래서 detect_all 결과와 무관하게 항상 시도)
    findings += template_fingerprint.suggest_candidates(full_text, spans, sizes, findings)
    if not findings:
        print(f"[{src.name}] 탐지된 개인정보가 없습니다.")
        return 1

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

    print(f"[{src.name}] {len(findings)}건 탐지됨 -> 검토 화면 표시")
    retry_notice: str | None = None
    highlight_spans: set[tuple[int, int]] = set()

    while True:
        reviewed = run_review(src.name, findings, full_text, spans, total_pages, str(src),
                               _auto_approve_after_ms=_auto_approve_after_ms,
                               retry_notice=retry_notice, highlight_spans=highlight_spans,
                               page_sizes=sizes, warning_pages=warning_pages)
        if reviewed is None:
            print(f"[{src.name}] 검토가 취소되었습니다. 처리하지 않음.")
            return 2
        findings = reviewed.findings

        result = process_atomic(str(src), findings, str(workspace),
                                 processor=processor, doc_type=reviewed.doc_type)
        if result.success or result.error != "self_check_failed":
            break

        # 6.5.1: 재검증 실패 -> 저장 차단. 실제 값은 화면/콘솔 어디에도 노출하지 않고
        # (6.5.3) 건수만 안내한 뒤, 검토 화면으로 복귀해 해당 항목을 하이라이트
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

    if not result.success:
        print(f"[{src.name}] 원본 이동/마커/로그/리포트 기록 실패 — 원본은 원래 위치로 되돌렸습니다. ({result.error})")
        return 5

    print(f"[{src.name}] 마스킹 완료 -> {result.output_path}")
    print(f"  원본 이동 -> {result.original_moved_to}")
    print(f"  유형별 건수: {result.masked_counts}")
    if result.rotated_text_warning:
        print("  ⚠ 회전된 텍스트가 있습니다. 자동 탐지가 놓쳤을 수 있으니 육안으로 재확인하세요.")
    if result.hidden_content_warning:
        print("  ⚠ 주석/폼필드/첨부파일 등 숨겨진 콘텐츠가 있습니다. 별도 확인이 필요합니다.")

    # 6.3.2 ②: 처리 이력 조회 UI(설계서 11번, 미구현)가 아직 없으므로, 이 MVP
    # 범위에서는 처리 성공 직후 물어보는 방식으로 원본-마스킹본 비교를 연결.
    # _auto_processor_name/_auto_reprocess_confirm과 같은 관례: 헤드리스 테스트가
    # _auto_open_comparison을 명시하면 그 값을 그대로 답으로 쓰고, 생략하면(None)
    # 실제 대화형 사용이라고 보고 진짜 QMessageBox로 물어봄.
    if _auto_open_comparison is not None:
        open_comparison = _auto_open_comparison
    else:
        reply = QMessageBox.question(
            None, "결과 비교",
            "처리가 완료되었습니다. 원본과 결과물을 나란히 비교해서 확인하시겠습니까?\n"
            "(⚠ 원본_보관/의 원본 파일을 다시 엽니다 — 개인정보를 포함하고 있습니다)",
        )
        open_comparison = reply == QMessageBox.StandardButton.Yes
    if open_comparison:
        compare_view.open_result_comparison(result.original_moved_to, result.output_path)

    return 0


def process_batch(
    input_paths: list[str], workspace_dir: str | None = None,
    _auto_approve_after_ms: int | None = None,
) -> dict[str, int]:
    """여러 파일을 대기열로 순차 처리 (7번 정책표).

    배치 자동 처리가 아니라 파일마다 사람이 검토 화면에서 확인하는 구조 --
    이 함수는 process_file()을 파일 개수만큼 반복 호출할 뿐, 검토를 건너뛰지
    않음. 한 파일이 실패/취소돼도 나머지 파일 처리는 계속 진행.

    반환값: {입력 경로: process_file() 반환코드} 딕셔너리.
    """
    results: dict[str, int] = {}
    total = len(input_paths)
    for i, input_path in enumerate(input_paths, start=1):
        print(f"\n=== [{i}/{total}] {Path(input_path).name} 처리 시작 ===")
        results[input_path] = process_file(
            input_path, workspace_dir, _auto_approve_after_ms=_auto_approve_after_ms,
        )

    succeeded = sum(1 for rc in results.values() if rc == 0)
    print(f"\n=== 배치 처리 완료: {succeeded}/{total}건 마스킹 완료 ===")
    for path, rc in results.items():
        if rc != 0:
            reason = {
                1: "탐지된 개인정보 없음", 2: "검토 취소", 3: "자체검증 실패",
                4: "재실행 확인 후 취소됨", 5: "롤백됨", 6: "출력 폴더 준비 실패",
            }.get(rc, f"코드 {rc}")
            print(f"  - {Path(path).name}: {reason}")
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 main.py <입력1.pdf> [입력2.pdf ...]")
        print("  결과는 프로그램(.exe/스크립트) 옆에 원본_보관/ 마스킹완료/ 요약리포트/ 로그/ 로 모입니다.")
        sys.exit(1)
    batch_results = process_batch(sys.argv[1:])
    sys.exit(0 if all(rc == 0 for rc in batch_results.values()) else 1)
