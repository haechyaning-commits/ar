#!/usr/bin/env bash
# mvp/tests/의 전체 테스트를 순서대로 실행.
# UI/end-to-end 테스트는 화면이 필요하므로 디스플레이가 없는 환경에서는
# xvfb-run으로 감싸서 실행한다 (Xvfb 필요: apt install xvfb).
set -e
cd "$(dirname "$0")"

RUN_UI="python3"
if [ -z "$DISPLAY" ] && command -v xvfb-run >/dev/null; then
    RUN_UI="xvfb-run -a python3"
fi

echo "=== detector ==="; python3 test_detector.py
echo "=== masker ==="; python3 test_masker.py
echo "=== pipeline ==="; python3 test_pipeline.py
echo "=== review_ui (클릭) ==="; $RUN_UI test_review_ui_click.py
echo "=== 사전 되돌리기 UI (클릭) ==="; $RUN_UI test_dictionary_revert_ui.py
echo "=== 진행바 세부 보강(경고/필터) ==="; $RUN_UI test_page_progress_extras.py
echo "=== template_fingerprint ==="; python3 test_template_fingerprint.py
echo "=== compare_view (클릭) ==="; $RUN_UI test_compare_view.py
echo "=== 실사용자 편의 기능 (클릭) ==="; $RUN_UI test_convenience_features.py
echo "=== 검토창 내장 실제 페이지 캔버스 (클릭) ==="; $RUN_UI test_inline_review_canvas.py
echo "=== 처리 이력 조회 (클릭) ==="; $RUN_UI test_history_view.py
echo "=== end-to-end (클릭) ==="; $RUN_UI test_end_to_end_click.py

echo
echo "전체 테스트 통과"
