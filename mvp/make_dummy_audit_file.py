"""
end-to-end 테스트용 더미 감사파일(지출결의서 형태)을 생성.
전부 가짜 데이터 - 실제 개인정보 아님 (2.1 선행 조건).
"""
import fitz

doc = fitz.open()
page = doc.new_page()

lines = [
    "지출결의서",
    "",
    "신청인: 김테스트 (010-1234-5678)",
    "주민등록번호: 900101-1234568",
    "입금계좌: 국민은행 123456-04-789012 (예금주: 김테스트)",
    "이메일: test.dummy@example.com",
    "주소: 서울특별시 강남구 테헤란로 123",
    "",
    "지출 내역: 2026년 3월 출장비 정산",
    "",
    "기안자: 이감사 / 검토: 박팀장 / 결재: 최과장",
    "",
    "비고: 본 문서는 테스트용 더미 데이터이며 실제 개인정보를 포함하지 않습니다.",
]

y = 72
for line in lines:
    page.insert_text((72, y), line, fontsize=11, fontname="korea")
    y += 20

doc.set_metadata({"author": "김테스트", "title": "지출결의서_김테스트_3월"})
doc.save("_dummy_audit_file.pdf")
doc.close()
print("생성 완료: _dummy_audit_file.pdf")
