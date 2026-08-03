"""
pipeline.py 원자성 스트레스 테스트 -- 실제(모킹 아닌) 실패를 유발해서 롤백을 검증한다.

- 파일명 충돌 처리
- 로그 폴더를 진짜로 쓰기 불가능하게 만들어(chattr +i) 커밋 중 실패 -> 롤백 확인
- 워크스페이스 사전조건이 깨진 경우(원본_보관 자리에 파일이 있음)의 동작
- 여러 번 연속 처리했을 때 로그가 정상적으로 누적되는지

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (설계서 2.1 선행 조건). root로 실행되는 환경에서는
파일 권한(chmod)만으로는 쓰기를 막을 수 없어 chattr(불변 속성)을 사용한다 -- 해당 파일시스템이
chattr을 지원하지 않으면 이 케이스는 건너뛴다.
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz

from detector import detect_all
from pdf_extract import extract_text_and_spans
from pipeline import process_atomic

_FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not cond:
        _FAILURES.append(f"{label}: {detail}")


def _new_tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="pipeline_test_"))


def _make_dummy_pdf(path: Path, name_value: str = "김테스트"):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), f"신청인: {name_value} (010-1234-5678)", fontsize=11, fontname="korea")
    doc.save(path)
    doc.close()


def _detect(path: Path):
    doc = fitz.open(path)
    # masker.py(process_atomic이 내부적으로 호출)는 pdf_extract.extract_text_and_spans()의
    # 오프셋 기준으로 위치를 되짚으므로, 탐지도 같은 함수를 써야 offset이 안 어긋난다.
    full_text, _spans = extract_text_and_spans(doc)
    doc.close()
    return detect_all(full_text)


def _chattr_supported(directory: Path) -> bool:
    r = subprocess.run(["chattr", "+i", str(directory)], capture_output=True)
    if r.returncode != 0:
        return False
    subprocess.run(["chattr", "-i", str(directory)], capture_output=True)
    return True


# ---------------------------------------------------------------------------
def test_filename_collision_gets_numbered_not_overwritten():
    print("test_filename_collision_gets_numbered_not_overwritten")
    ws = _new_tmp()
    src_dir = _new_tmp()

    src1 = src_dir / "지출결의서.pdf"
    _make_dummy_pdf(src1, "김테스트")
    r1 = process_atomic(str(src1), _detect(src1), str(ws))
    check("1차 처리 성공", r1.success)

    # 원본_보관/에 같은 이름의 파일을 다시 두고 두 번째 처리 (충돌 유발)
    src2 = src_dir / "지출결의서.pdf"
    _make_dummy_pdf(src2, "박테스트")
    r2 = process_atomic(str(src2), _detect(src2), str(ws))
    check("2차 처리도 성공(파일명 겹쳐도 처리 자체는 됨)", r2.success)

    check("원본_보관에 두 원본이 서로 다른 이름으로 모두 보존됨(덮어쓰기 없음)",
          r1.original_moved_to != r2.original_moved_to
          and Path(r1.original_moved_to).exists() and Path(r2.original_moved_to).exists(),
          f"{r1.original_moved_to} vs {r2.original_moved_to}")
    check("마스킹완료에도 두 결과물이 서로 다른 이름으로 모두 존재함",
          r1.output_path != r2.output_path
          and Path(r1.output_path).exists() and Path(r2.output_path).exists(),
          f"{r1.output_path} vs {r2.output_path}")


def test_log_accumulates_across_multiple_runs():
    print("test_log_accumulates_across_multiple_runs")
    ws = _new_tmp()
    src_dir = _new_tmp()

    for i in range(3):
        src = src_dir / f"file{i}.pdf"
        _make_dummy_pdf(src, "김테스트")
        r = process_atomic(str(src), _detect(src), str(ws))
        check(f"{i+1}번째 처리 성공", r.success)

    log_path = ws / "로그" / "audit_log.csv"
    with open(log_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    check("로그에 3건이 누적됨(헤더 중복 없이)", len(rows) == 3, f"{len(rows)}건")
    check("로그에 실제 이름/전화번호 값이 남지 않음(6.5.3/6.7 -- 유형:건수만)",
          all("김테스트" not in r["masked_counts"] and "010-1234-5678" not in r["masked_counts"] for r in rows),
          str([r["masked_counts"] for r in rows]))
    check("각 로그 행에 원본 SHA-256 해시가 64자 hex로 기록됨",
          all(len(r["original_sha256"]) == 64 for r in rows))


def test_workspace_precondition_broken_fails_cleanly():
    print("test_workspace_precondition_broken_fails_cleanly")
    ws = _new_tmp()
    src_dir = _new_tmp()
    # "원본_보관" 자리에 디렉토리가 아닌 일반 파일을 미리 만들어 mkdir을 깨뜨림
    (ws / "원본_보관").write_text("이 자리는 폴더여야 하는데 파일임")

    src = src_dir / "file.pdf"
    _make_dummy_pdf(src)
    # 수정 전: mkdir()이 process_atomic()의 try 블록 밖에 있어 예외가 호출자(main.py)까지
    # 그대로 전파돼 사용자에게 파이썬 트레이스백이 노출됐다. mkdir을 자체 try/except로
    # 감싸도록 고쳐서, 지금은 예외 없이 깔끔한 실패 결과가 돌아온다.
    result = process_atomic(str(src), _detect(src), str(ws))
    check("예외 없이 깔끔한 실패 결과가 돌아옴(트레이스백 노출 안 됨)", not result.success)
    check("에러 사유가 workspace_setup_failed로 분류됨", result.error.startswith("workspace_setup_failed"), result.error)
    check("원본은 그대로 남아있음(옮겨지지 않음)", src.exists())


def test_real_commit_failure_via_immutable_log_dir():
    print("test_real_commit_failure_via_immutable_log_dir")
    ws = _new_tmp()
    src_dir = _new_tmp()
    log_dir = ws / "로그"
    log_dir.mkdir(parents=True)

    if not _chattr_supported(log_dir):
        print("  [SKIP] 이 파일시스템은 chattr(불변 속성)을 지원하지 않아 건너뜀")
        return

    src = src_dir / "file.pdf"
    _make_dummy_pdf(src)
    original_bytes = src.read_bytes()

    subprocess.run(["chattr", "+i", str(log_dir)], check=True)
    try:
        result = process_atomic(str(src), _detect(src), str(ws))
    finally:
        subprocess.run(["chattr", "-i", str(log_dir)], check=True)

    check("커밋 실패로 success=False", not result.success, str(result.error))
    check("원본이 원래 위치로 복구됨(내용도 동일)",
          src.exists() and src.read_bytes() == original_bytes)
    check("마스킹완료/에 부분 산출물이 남지 않음",
          not any((ws / "마스킹완료").iterdir()) if (ws / "마스킹완료").exists() else True)
    check("원본_보관/에도 부분 산출물이 남지 않음",
          not any((ws / "원본_보관").iterdir()) if (ws / "원본_보관").exists() else True)
    check("로그 파일이 생성되지 않음", not (log_dir / "audit_log.csv").exists())


def main():
    tests = [
        test_filename_collision_gets_numbered_not_overwritten,
        test_log_accumulates_across_multiple_runs,
        test_workspace_precondition_broken_fails_cleanly,
        test_real_commit_failure_via_immutable_log_dir,
    ]
    for t in tests:
        t()

    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)}건")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"모든 pipeline 테스트 통과 ({len(tests)}개 시나리오)")
    sys.exit(0)


if __name__ == "__main__":
    main()
