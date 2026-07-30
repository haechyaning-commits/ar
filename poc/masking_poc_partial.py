"""
부분마스킹(예: 김테스트 -> 김*테스트, 010-1234-5678 -> 010-****-5678)이
실제로 리댁션 + 텍스트 재삽입 방식으로 기술적으로 가능한지 검증.
더미 데이터만 사용.
"""
import fitz
import re

doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 72), "신청인: 김테스트 (010-1234-5678)", fontsize=12, fontname="korea")
src = "/tmp/claude-0/-home-user-ar/90d1b6b9-8b97-5c8e-8cb2-62b3f1be5ad3/scratchpad/_dummy_partial_src.pdf"
doc.save(src)
doc.close()

doc = fitz.open(src)
page = doc[0]

def partial_mask_name(name):
    # 홍길동 -> 홍*동 / 2글자면 첫글자만 남기고 마스킹
    if len(name) <= 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]

def partial_mask_phone(phone):
    # 010-1234-5678 -> 010-****-5678
    parts = phone.split("-")
    if len(parts) == 3:
        parts[1] = "*" * len(parts[1])
        return "-".join(parts)
    return phone

name_original = "김테스트"
phone_original = "010-1234-5678"
name_masked = partial_mask_name(name_original)
phone_masked = partial_mask_phone(phone_original)

print(f"이름: {name_original} -> {name_masked}")
print(f"전화번호: {phone_original} -> {phone_masked}")

for original, masked in [(name_original, name_masked), (phone_original, phone_masked)]:
    rects = page.search_for(original)
    print(f"'{original}' 위치 찾음: {len(rects)}건")
    for r in rects:
        # 리댁션 + 같은 자리에 마스킹된 텍스트 재삽입
        page.add_redact_annot(r, text=masked, fontname="korea", fontsize=11, align=fitz.TEXT_ALIGN_LEFT)
page.apply_redactions()

result_text = page.get_text()
print("\n=== 부분마스킹 후 추출된 텍스트 ===")
print(result_text)

ok_name = name_masked in result_text and name_original not in result_text
ok_phone = phone_masked in result_text and phone_original not in result_text
print(f"\n[{'PASS' if ok_name else 'FAIL'}] 이름 부분마스킹이 정확히 반영됨 (원본은 사라지고 마스킹된 형태만 존재)")
print(f"[{'PASS' if ok_phone else 'FAIL'}] 전화번호 부분마스킹이 정확히 반영됨")

out = "/tmp/claude-0/-home-user-ar/90d1b6b9-8b97-5c8e-8cb2-62b3f1be5ad3/scratchpad/_dummy_partial_out.pdf"
doc.save(out)
doc.close()

# 원본 바이트 레벨에서도 진짜 사라졌는지 확인
with open(out, "rb") as f:
    raw = f.read()
print(f"\n[{'PASS' if name_original.encode() not in raw else 'FAIL'}] 원본 이름이 PDF 바이트에 안 남음")
print(f"[{'PASS' if phone_original.encode() not in raw else 'FAIL'}] 원본 전화번호가 PDF 바이트에 안 남음")
