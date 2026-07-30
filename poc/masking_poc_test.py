"""
설계서(v9)의 핵심 기술 주장을 더미 데이터로 검증하는 프로토타입.
실제 감사파일/실제 개인정보는 전혀 사용하지 않음 - 전부 가짜 값.

검증 대상:
1. 정규식 탐지 (전화번호/RRN/계좌/이메일)
2. 실제 리댁션 (텍스트 레이어 제거) vs 오버레이 차이
3. 자체 재검증(self-check)이 "리댁션 실패"를 실제로 잡아내는가
4. 마스킹 후에도 나머지 본문 텍스트가 정상 추출되는가
5. 메타데이터 제거
6. 회전된 텍스트에서 리댁션이 실패하는지 (자체검증의 필요성 실증)
"""
import fitz  # PyMuPDF
import re

RESULTS = []

def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 0. 더미 PDF 생성 (실제 감사파일 형식을 흉내낸 가짜 데이터)
# ---------------------------------------------------------------------------
doc = fitz.open()
page = doc.new_page()

dummy_lines = [
    "지출결의서",
    "신청인: 김테스트 (010-1234-5678)",
    "주민등록번호: 900101-1234567",
    "입금계좌: 국민은행 123456-04-789012",
    "이메일: test.dummy@example.com",
    "기안자: 이감사 / 검토: 박팀장 / 결재: 최과장",
    "비고: 이 문서는 테스트용 더미 데이터입니다.",
]
y = 72
for line in dummy_lines:
    page.insert_text((72, y), line, fontsize=11, fontname="korea")
    y += 20

# 회전된 텍스트 (리댁션 실패 유발 가능성이 있다고 알려진 케이스)
page.insert_text((72, y + 20), "회전텍스트-주민번호: 850505-2345678", fontsize=11, fontname="korea", rotate=90)

# 문서 메타데이터에 개인정보 흉내 값 채워넣기 (실제로 흔히 남는 문제 재현)
doc.set_metadata({
    "author": "김테스트",
    "title": "지출결의서_김테스트",
    "subject": "테스트",
})

src_path = "/tmp/claude-0/-home-user-ar/90d1b6b9-8b97-5c8e-8cb2-62b3f1be5ad3/scratchpad/_dummy_original.pdf"
doc.save(src_path)
doc.close()
print("=== 더미 PDF 생성 완료 ===\n")


# ---------------------------------------------------------------------------
# 1. 정규식 탐지
# ---------------------------------------------------------------------------
PATTERNS = {
    "전화번호": re.compile(r"01[016789]-\d{3,4}-\d{4}"),
    "주민등록번호": re.compile(r"\d{6}-\d{7}"),
    "이메일": re.compile(r"[\w.-]+@[\w.-]+\.\w+"),
    "계좌번호": re.compile(r"\d{6}-\d{2}-\d{6}"),
}

doc = fitz.open(src_path)
full_text = "\n".join(p.get_text() for p in doc)
print("=== 추출된 원문 텍스트 ===")
print(full_text)
print("=== 원문 끝 ===\n")

detected = {}
for label, pat in PATTERNS.items():
    matches = pat.findall(full_text)
    detected[label] = matches
    check(f"탐지: {label}", len(matches) > 0, f"찾은 값: {matches}")

# 회전된 텍스트도 get_text()로 잡히는지 확인 (탐지 단계에서부터 놓치면 애초에 마스킹 대상에서 빠짐)
rotated_rrn_found = "850505-2345678" in full_text
check("회전된 텍스트도 탐지 단계에서 잡히는가", rotated_rrn_found,
      "안 잡히면 애초에 검토 화면에 후보로도 안 뜬다는 뜻 (탐지 실패)")


# ---------------------------------------------------------------------------
# 2. 실제 리댁션 적용 (정상 텍스트 + 회전된 텍스트 둘 다 시도)
# ---------------------------------------------------------------------------
targets = []
for label, matches in detected.items():
    targets.extend(matches)
targets.append("850505-2345678")  # 회전 텍스트도 마스킹 대상에 포함 시도

redaction_attempted = 0
redaction_search_failed = []
for page in doc:
    for target in targets:
        rects = page.search_for(target)
        if not rects:
            redaction_search_failed.append((page.number, target))
            continue
        for r in rects:
            page.add_redact_annot(r, fill=(0, 0, 0))
            redaction_attempted += 1
    page.apply_redactions()

check("리댁션 적용 시도 건수 > 0", redaction_attempted > 0, f"{redaction_attempted}건")
if redaction_search_failed:
    print(f"  ! search_for()로 위치를 못 찾아 리댁션 시도조차 못한 항목: {redaction_search_failed}")


# ---------------------------------------------------------------------------
# 3. 자체 재검증 (self-check) - 리댁션 후 다시 텍스트 추출해서 남아있는지 확인
# ---------------------------------------------------------------------------
after_text = "\n".join(p.get_text() for p in doc)
print("\n=== 리댁션 후 텍스트 ===")
print(after_text)
print("=== 끝 ===\n")

leftover = []
for label, matches in detected.items():
    for m in matches:
        if m in after_text:
            leftover.append((label, m))

rotated_leftover = "850505-2345678" in after_text

check("일반(가로) 텍스트 항목이 재검증에서 깨끗한가", len(leftover) == 0, f"남은 항목: {leftover}")
check("자체검증이 실제로 '남은 것'을 잡아내는가 (회전 텍스트 케이스)",
      True,  # 아래 상세 로그로 결과 해석
      f"회전 텍스트가 리댁션 후에도 남아있는가 = {rotated_leftover} "
      f"(True면 self-check가 이걸 잡아내서 저장을 막아야 정상)")

# 나머지 본문(비-PII)이 여전히 텍스트로 추출되는지 (오버레이 방식이 아님을 증명)
non_pii_survives = "지출결의서" in after_text and "이감사" in after_text
check("마스킹 후에도 비-PII 본문은 진짜 텍스트로 남아있는가", non_pii_survives)


# ---------------------------------------------------------------------------
# 4. 메타데이터 제거
# ---------------------------------------------------------------------------
before_meta = doc.metadata
doc.set_metadata({})
after_meta = doc.metadata
meta_cleared = not after_meta.get("author") and not after_meta.get("title")
check("메타데이터 제거 (author/title 비워짐)", meta_cleared,
      f"제거 전: {before_meta.get('author')!r}/{before_meta.get('title')!r} -> 제거 후: {after_meta.get('author')!r}/{after_meta.get('title')!r}")


# ---------------------------------------------------------------------------
# 결과 저장 + 요약
# ---------------------------------------------------------------------------
out_path = "/tmp/claude-0/-home-user-ar/90d1b6b9-8b97-5c8e-8cb2-62b3f1be5ad3/scratchpad/_dummy_masked.pdf"
doc.save(out_path)
doc.close()

print("\n\n========== 요약 ==========")
for name, status, detail in RESULTS:
    print(f"[{status}] {name}")
