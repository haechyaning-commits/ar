# 감사파일 개인정보 마스킹 자동화

감사실 감사파일(hwp/hwpx/pdf)에 포함된 개인정보를 수기로 하나씩 마스킹하는 작업을 줄이기 위한 도구입니다.
완전 자동화가 아니라 **"자동 탐지 + 사람의 최종 검토/승인"** 구조로 설계되어 있습니다 — 이름/주소 같은 항목은 자동 탐지가 100% 정확할 수 없다는 전제하에, 사람이 검토 단계에서 최종 확인합니다.

개인 프로젝트로 진행 중이며, 실제 감사파일은 아직 사용하지 않습니다 (자세한 내용은 설계서 2.1 참고).

## 현재 상태

**PDF 입력 기준 MVP 구현 및 end-to-end 검증 완료 (출력/로그/리포트 포함 전체 파이프라인).**

- 아키텍처 설계는 v13까지 정리됨 (타당성 평가 + 미검증 항목 실측 보완 포함, 12.5절)
- PDF 파일을 넣으면 탐지 → 재실행 방지 확인 → 검토(문서유형 선택 포함) → 마스킹 → 자체검증 → 처리완료 마커 → 원본 보관(해시 기록) → 감사로그 → 요약리포트까지 전체 파이프라인이 실제로 동작함 (더미 데이터로 검증)
- 개발 중 발견한 버그 다수를 실제 재현 테스트로 확인 후 수정함 (예: 마스킹 텍스트 폰트 크기 축소, 전화번호/계좌번호 이중탐지, 이메일 1글자 로컬파트 미마스킹, 지역명이 다른 단어의 부분 문자열로 들어있을 때 주소 오탐 등)
- 이름/주소 탐지 정확도를 12개 시나리오 더미 코퍼스로 실측: **이름 precision 93%/recall 82%, 주소 precision 80%/recall 100%** (`mvp/evaluate_detection_accuracy.py`)
- Linux에서 PyInstaller 단일 실행파일 빌드를 실제로 수행해 패키징 메커니즘(의존성/데이터파일 번들링)을 검증함 — 단, 실제 배포 대상인 **Windows용 .exe는 미검증**
- 헤드리스(offscreen Qt) 환경에서 검토 UI를 포함한 전체 파이프라인 통합 테스트를 실행해 도구 자체의 처리 시간(5페이지·54건 기준 평균 0.1초 미만)을 실측함
- **아직 없는 것**: hwp/hwpx → PDF 변환(한글/Windows 환경 필요, 미착수), Windows용 .exe 실제 빌드, 실제 화면으로 클릭해보는 인터랙티브 테스트(이 개발 환경엔 디스플레이가 없어 헤드리스로만 검증함)

## 문서

- [`docs/masking-architecture.md`](docs/masking-architecture.md) — 아키텍처 설계서. 전체 처리 흐름, 계층별 설계, 결정된 정책, 알려진 리스크, PoC 검증 결과, 타당성 평가(성공기준/엔지니어링/평가/진행여부)를 담고 있습니다.

## MVP

PDF 입력만 지원하는 최소 동작 버전입니다 (hwp/hwpx 변환 어댑터는 범위 밖).

- [`mvp/detector.py`](mvp/detector.py) — 개인정보 탐지 엔진. 전화번호/이메일/계좌번호/주민등록번호(체크섬 검증)/여권번호는 정규식, 이름/주소는 사전+문맥규칙 기반
- [`mvp/masker.py`](mvp/masker.py) — 실제 리댁션(오버레이 아님) + 항목별 부분/완전마스킹 + 저장 전 자체 재검증 + 메타데이터/OCG·주석 등 숨김 콘텐츠 확인
- [`mvp/review_ui.py`](mvp/review_ui.py) — 최소 검토 화면 (PySide6). 기본 전체 승인 상태에서 예외만 체크 해제 + 문서 유형 선택
- [`mvp/output.py`](mvp/output.py) — 출력 계층: 파일명 규칙/폴더 구조, 원본 SHA-256 해시 기록, 감사로그(CSV), 요약리포트, 재실행 방지 마커, 처리자 식별
- [`mvp/main.py`](mvp/main.py) — 위 모듈들을 잇는 진입점
- [`mvp/data/`](mvp/data/) — 성씨/제외어/행정구역 사전
- [`mvp/evaluate_detection_accuracy.py`](mvp/evaluate_detection_accuracy.py) — 이름/주소 탐지 정밀도·재현율 실측 스크립트 (더미 코퍼스 12개 시나리오)

**실행 방법** (디스플레이가 있는 PC에서):
```
pip install pymupdf pyside6
python3 mvp/main.py 파일.pdf
```
탐지된 항목을 보여주는 검토 창이 뜨고, 문서 유형을 선택 후 승인하면 `마스킹완료/[문서유형]_[처리일자]_[일련번호].pdf`가 생성되고, 원본은 `원본_보관/`으로 이동, `로그/`에 감사로그, `요약리포트/`에 처리 요약이 함께 생성됩니다.

**정확도 측정 재현**:
```
pip install pymupdf pyside6
cd mvp && python3 evaluate_detection_accuracy.py
```

**패키징 (Linux에서 검증됨, Windows는 별도 검증 필요)**:
```
pip install pyinstaller
cd mvp && pyinstaller --onefile --name MaskingTool --add-data "data:data" main.py
```
Windows에서는 `--add-data` 구분자가 `:`가 아닌 `;`입니다 (`--add-data "data;data"`).

## PoC (Proof of Concept)

MVP 이전에, 더미 데이터로 핵심 기술 전제("마스킹이 오버레이가 아니라 진짜로 지워지는가")만 좁게 검증했던 초기 스크립트입니다. 실제 감사파일은 사용하지 않습니다.

- [`poc/masking_poc_test.py`](poc/masking_poc_test.py) — 정규식 탐지, 실제 리댁션(오버레이 아님), 자체 재검증, 메타데이터 제거, 회전된 텍스트 실패 케이스 검증
- [`poc/masking_poc_partial.py`](poc/masking_poc_partial.py) — 부분마스킹(`홍길동` → `홍*동`)이 리댁션 + 텍스트 재삽입으로 정상 렌더링되는지 검증
