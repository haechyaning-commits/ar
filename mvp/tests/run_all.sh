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
echo "=== template_fingerprint ==="; python3 test_template_fingerprint.py
echo "=== compare_view (클릭) ==="; $RUN_UI test_compare_view.py
echo "=== 실사용자 편의 기능 (클릭) ==="; $RUN_UI test_convenience_features.py
echo "=== end-to-end (클릭) ==="; $RUN_UI test_end_to_end_click.py

echo
echo "전체 테스트 통과"
