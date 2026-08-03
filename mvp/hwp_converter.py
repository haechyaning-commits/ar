"""
hwp/hwpx -> PDF 변환 어댑터 (6.1).

pywin32로 한/글(한컴오피스) COM을 열어 "다른 이름으로 저장 -> PDF"를 호출.
Windows + 한컴오피스 설치 환경에서만 동작 -- 이 리포지토리의 개발 환경(Linux)에서는
pywin32 자체를 설치/실행할 수 없어 실동작 검증이 불가능함 (2.1, 8번 리스크).
골격만 작성한 상태이며, 실제 변환 동작·에러 메시지 매칭은 Windows + 한컴오피스
PC에서 확인 후 보정이 반드시 필요함.

실제 감사파일이 아닌 더미 데이터로만 검증할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ConversionResult:
    success: bool
    pdf_path: str | None = None
    error_reason: str | None = None  # 실패 시 사용자 화면에 그대로 표시할 사유 (6.1)


# 한/글 COM이 던지는 예외 메시지에서 6.1이 요구하는 4가지 실패 케이스를 구분하기
# 위한 키워드 매칭. TODO(Windows 실측 필요): 실제 예외 메시지/HRESULT를
# 한컴오피스 PC에서 재현해 확인한 뒤 정확한 값으로 교체할 것 -- 지금은 자리만
# 잡아둔 추정값이라 실제 메시지와 다를 수 있음.
_KNOWN_FAILURE_PATTERNS: dict[str, list[str]] = {
    "매크로 보안 경고로 자동화가 차단됨": ["macro", "매크로", "보안"],
    "비밀번호로 잠긴 파일": ["password", "암호"],
    "한/글이 이미 다른 작업으로 사용 중": ["already", "in use", "다른 프로그램"],
    "손상되었거나 지원하지 않는 파일 형식": ["invalid", "corrupt", "형식"],
}


def _classify_failure(exc: Exception) -> str:
    message = str(exc)
    for reason, keywords in _KNOWN_FAILURE_PATTERNS.items():
        if any(k.lower() in message.lower() for k in keywords):
            return reason
    return f"변환 실패 (원인 미분류, 로그 확인 필요): {message}"


def convert_to_pdf(input_path: str, output_dir: str | Path) -> ConversionResult:
    """hwp/hwpx 파일 1건을 output_dir에 PDF로 변환.

    실패해도 원본은 절대 건드리지 않고, 이 파일만 실패로 반환한다 -- 호출하는
    쪽(대기열 처리)에서 나머지 파일 처리를 계속 진행할 수 있어야 함 (6.1).
    """
    if sys.platform != "win32":
        return ConversionResult(
            False, None,
            "hwp/hwpx 변환은 Windows + 한컴오피스 환경에서만 지원됩니다.",
        )

    import win32com.client  # Windows 전용 라이브러리라 여기서만 지연 import

    src = Path(input_path)
    dst = Path(output_dir) / f"{src.stem}.pdf"

    hwp = None
    try:
        hwp = win32com.client.gencache.EnsureDispatch("HWPFrame.HwpObject")
        # 자동화 호출 시 뜨는 보안 확인창을 넘기기 위한 등록.
        # TODO(Windows 실측 필요): 이 등록만으로 매크로 보안 경고까지 우회되는지,
        # 아니면 매크로 포함 문서는 여전히 별도 처리(실패 케이스)가 필요한지 확인.
        hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModuleExample")
        hwp.Open(str(src))
        hwp.SaveAs(str(dst), "PDF")
        return ConversionResult(True, str(dst))
    except Exception as exc:  # COM 예외 유형이 다양해 넓게 잡은 뒤 원인별로 분류
        return ConversionResult(False, None, _classify_failure(exc))
    finally:
        if hwp is not None:
            try:
                hwp.Clear(1)
                hwp.Quit()
            except Exception:
                pass  # 종료 실패가 변환 성공/실패 판정에 영향을 주지 않도록 함
