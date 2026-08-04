"""
문서 템플릿 지문 인식 (6.2.2).

감사실 문서는 지출결의서/출장복명서처럼 반복되는 정해진 양식이 많다는 특성을
활용. 문서를 처음(승인 시점) 처리할 때, 검토자가 승인한 개인정보 항목들의
"주변 라벨 + 페이지 내 상대좌표"를 그 문서의 레이아웃과 함께 지문으로 저장한다.
같은 양식이 다시 들어오면(레이아웃 유사도로 판별), 저장된 지문 근처에 현재
탐지 결과가 없는 위치를 찾아 "이 양식에서는 통상 여기에 있었다"는 근거로
검토자에게 우선 확인 후보(그룹 "템플릿후보")로 추가 제시한다.

6.2.1(사전 자동 학습)과 같은 결로, 검토자가 이미 하는 행동(승인)만으로 조용히
지문을 축적한다 -- 별도로 "이 문서를 템플릿으로 등록하시겠습니까?" 같은 질문을
하지 않음. 6.7의 로그(자동탐지/수동추가 출처 구분)와 마찬가지로 기존 검토
흐름에서 나오는 데이터를 재활용하는 저비용 기능.

레이아웃 유사도 판별: 문서 안의 "라벨:" 패턴(성명/전화번호/계좌 등 detector.py가
이미 쓰는 라벨 어휘)의 (페이지, 상대x, 상대y) 위치를 앵커로 삼아, 두 문서의
앵커 집합이 얼마나 겹치는지(Jaccard 유사도)로 판단한다. 앵커는 라벨 텍스트
자체이지 값(개인정보)이 아니므로, 같은 양식의 서로 다른 사본이라면 가운데 값만
바뀌고 라벨 위치는 그대로인 특성을 그대로 이용할 수 있다.

실제 감사파일이 아닌 더미 데이터로만 테스트할 것 (2.1 선행 조건).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from detector import ADDRESS_LABELS, BUSINESS_LABELS, NAME_LABELS, Finding
from pdf_extract import SpanRef, spans_covering

DEFAULT_DATA_DIR = Path(__file__).parent / "data"
STORE_FILENAME = "template_fingerprints.json"

# 라벨 앵커로 쓸 어휘 -- 이름/주소 탐지가 쓰는 라벨에, 정규식 기반 탐지(전화/계좌/
# 카드/이메일/주민등록번호/여권번호)의 문맥 단서 라벨을 더한 것. 값이 아니라
# "이 위치에 어떤 필드가 있다"는 표식이므로 detector.py의 라벨 어휘를 그대로 재사용.
LABEL_WORDS = sorted(
    set(NAME_LABELS) | set(BUSINESS_LABELS) | set(ADDRESS_LABELS)
    | {"전화번호", "연락처", "발신자", "계좌", "입금", "카드", "이메일", "주민등록번호", "여권번호"},
    key=len, reverse=True,
)
_LABEL_RE = re.compile("(" + "|".join(re.escape(w) for w in LABEL_WORDS) + r")\s*[:：]")

# 좌표를 이 격자 크기(페이지 크기 대비 비율)로 반올림해서 "거의 같은 위치"를
# 하나로 취급 -- 사본마다 폰트 렌더링/여백이 미세하게 달라도 매칭되도록 함.
GRID = 0.03
# 두 문서를 같은 템플릿으로 볼 최소 앵커 유사도(Jaccard). 낮추면 다른 양식끼리도
# 잘못 묶일 위험이, 높이면 사소한 차이로 같은 양식을 못 알아볼 위험이 커짐.
MATCH_THRESHOLD = 0.6
# 유사도 계산이 우연으로 흔들리지 않으려면 최소한 이만큼의 공통 앵커가 있어야 함.
MIN_SHARED_ANCHORS = 2
# 이미 탐지된 항목과 "같은 자리"로 볼 격자 허용오차(칸 수)
POSITION_TOLERANCE = 1


def _bucket(value: float, size: float) -> int:
    if size <= 0:
        return 0
    return round((value / size) / GRID)


def _label_before(full_text: str, pos: int) -> str:
    """pos 앞 문맥에서 가장 가까운 라벨을 찾는다 (없으면 빈 문자열)."""
    line_start = full_text.rfind("\n", 0, pos) + 1
    window = full_text[line_start:pos]
    match = None
    for m in _LABEL_RE.finditer(window):
        match = m
    return match.group(1) if match else ""


def _span_center(spans: list[SpanRef], start: int, end: int) -> tuple[int, float, float] | None:
    covering = spans_covering(spans, start, end)
    if not covering:
        return None
    span = covering[0]
    x0, y0, x1, y1 = span.bbox
    return span.page_index, (x0 + x1) / 2, (y0 + y1) / 2


@dataclass
class Signature:
    page_count: int
    anchors: frozenset[tuple[int, str, int, int]]  # (page_index, label, x_bucket, y_bucket)


def build_signature(full_text: str, spans: list[SpanRef], sizes: list[tuple[float, float]]) -> Signature:
    anchors: set[tuple[int, str, int, int]] = set()
    for m in _LABEL_RE.finditer(full_text):
        center = _span_center(spans, m.start(), m.end())
        if center is None:
            continue
        page_index, cx, cy = center
        if page_index >= len(sizes):
            continue
        width, height = sizes[page_index]
        anchors.add((page_index, m.group(1), _bucket(cx, width), _bucket(cy, height)))
    return Signature(page_count=len(sizes), anchors=frozenset(anchors))


def _similarity(a: Signature, b: Signature) -> float:
    if not a.anchors or not b.anchors:
        return 0.0
    shared = a.anchors & b.anchors
    if len(shared) < MIN_SHARED_ANCHORS:
        return 0.0
    union = a.anchors | b.anchors
    return len(shared) / len(union)


def _load(data_dir: Path) -> list[dict]:
    path = data_dir / STORE_FILENAME
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(data_dir: Path, templates: list[dict]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / STORE_FILENAME
    with open(path, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


def _find_best_match(signature: Signature, templates: list[dict]) -> dict | None:
    best = None
    best_score = 0.0
    for tmpl in templates:
        tmpl_sig = Signature(
            page_count=tmpl["page_count"],
            anchors=frozenset(tuple(a) for a in tmpl["anchors"]),
        )
        score = _similarity(signature, tmpl_sig)
        if score >= MATCH_THRESHOLD and score > best_score:
            best, best_score = tmpl, score
    return best


def record_template(
    full_text: str, spans: list[SpanRef], sizes: list[tuple[float, float]],
    approved_findings: list[Finding], data_dir: Path | None = None,
) -> None:
    """검토자가 승인한(=실제로 마스킹될) 항목들의 위치를 이 문서의 레이아웃과
    함께 지문으로 저장. 같은 양식으로 매칭되는 기존 지문이 있으면 병합(항목을
    합집합으로 누적), 없으면 새 템플릿으로 등록한다.

    review_ui.py의 승인 버튼 클릭 시점에 호출됨 -- dictionary_learning과 마찬가지로
    최종 마스킹/저장 성공 여부와 무관하게, "검토자가 이 위치를 확정했다"는 사실
    자체가 신호이므로 여기서 조용히 기록한다.
    """
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR
    if not approved_findings or not spans or not sizes:
        return

    signature = build_signature(full_text, spans, sizes)
    if not signature.anchors:
        return  # 라벨 앵커가 없으면 이 문서로는 레이아웃을 특정할 수 없음

    items: list[dict] = []
    for f in approved_findings:
        center = _span_center(spans, f.start, f.end)
        if center is None:
            continue
        page_index, cx, cy = center
        if page_index >= len(sizes):
            continue
        width, height = sizes[page_index]
        items.append({
            "type": f.type,
            "page_index": page_index,
            "x_bucket": _bucket(cx, width),
            "y_bucket": _bucket(cy, height),
            "label": _label_before(full_text, f.start),
        })
    if not items:
        return

    templates = _load(data_dir)
    matched = _find_best_match(signature, templates)

    if matched is None:
        templates.append({
            "id": uuid.uuid4().hex[:12],
            "page_count": signature.page_count,
            "anchors": [list(a) for a in signature.anchors],
            "items": items,
            "sample_count": 1,
            "updated_at": datetime.now().isoformat(),
        })
    else:
        existing_keys = {
            (it["type"], it["page_index"], it["x_bucket"], it["y_bucket"]) for it in matched["items"]
        }
        for it in items:
            key = (it["type"], it["page_index"], it["x_bucket"], it["y_bucket"])
            if key not in existing_keys:
                matched["items"].append(it)
                existing_keys.add(key)
        matched["anchors"] = [list(a) for a in (signature.anchors | {tuple(a) for a in matched["anchors"]})]
        matched["sample_count"] = matched.get("sample_count", 1) + 1
        matched["updated_at"] = datetime.now().isoformat()

    _save(data_dir, templates)


def _already_covered(
    item_type: str, page_index: int, x_bucket: int, y_bucket: int,
    current_positions: list[tuple[str, int, int, int]],
) -> bool:
    for ftype, fpage, fx, fy in current_positions:
        if ftype != item_type or fpage != page_index:
            continue
        if abs(fx - x_bucket) <= POSITION_TOLERANCE and abs(fy - y_bucket) <= POSITION_TOLERANCE:
            return True
    return False


def _nearest_span(
    spans: list[SpanRef], page_index: int, x_bucket: int, y_bucket: int, sizes: list[tuple[float, float]],
) -> SpanRef | None:
    if page_index >= len(sizes):
        return None
    width, height = sizes[page_index]
    best, best_dist = None, None
    for span in spans:
        if span.page_index != page_index or not span.text.strip():
            continue
        x0, y0, x1, y1 = span.bbox
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        sx, sy = _bucket(cx, width), _bucket(cy, height)
        if abs(sx - x_bucket) > POSITION_TOLERANCE or abs(sy - y_bucket) > POSITION_TOLERANCE:
            continue
        dist = (sx - x_bucket) ** 2 + (sy - y_bucket) ** 2
        if best_dist is None or dist < best_dist:
            best, best_dist = span, dist
    return best


def _value_after_label_in_span(span: SpanRef, label: str) -> tuple[int, int, str] | None:
    """span.text 안에서 'label:' 뒤의 값 부분만 잘라 전역(start, end, value)로 돌려준다.
    라벨과 값이 같은 스팬 안에 함께 있는 경우(insert_text 한 번으로 넣은 "성명: 홍길동"
    같은 줄)가 보통이므로, 저장된 지문의 라벨을 다시 찾아 값만 후보로 제시 -- 그래야
    "성명:"이라는 라벨 텍스트까지 통째로 마스킹 후보가 되는 걸 피할 수 있음."""
    if label:
        m = re.search(re.escape(label) + r"\s*[:：]\s*", span.text)
        if m:
            value = span.text[m.end():].strip()
            if value:
                local_start = span.text.find(value, m.end())
                return span.start + local_start, span.start + local_start + len(value), value
    value = span.text.strip()
    if not value:
        return None
    local_start = span.text.find(value)
    return span.start + local_start, span.start + local_start + len(value), value


def suggest_candidates(
    full_text: str, spans: list[SpanRef], sizes: list[tuple[float, float]],
    findings: list[Finding], data_dir: Path | None = None,
) -> list[Finding]:
    """저장된 템플릿 지문 중 이 문서와 레이아웃이 일치하는 것이 있으면, 현재
    탐지 결과가 놓친 위치를 그룹 "템플릿후보"의 새 Finding으로 만들어 돌려준다.
    매칭되는 템플릿이 없거나 이미 탐지된 위치와 겹치면 빈 리스트를 돌려줌
    (탐지 엔진이 놓친 자리만 보강하는 게 목적이지, 이미 잡힌 항목을 중복
    제시하지 않음).
    """
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR
    if not spans or not sizes:
        return []

    templates = _load(data_dir)
    if not templates:
        return []

    signature = build_signature(full_text, spans, sizes)
    if not signature.anchors:
        return []

    matched = _find_best_match(signature, templates)
    if matched is None:
        return []

    current_positions: list[tuple[str, int, int, int]] = []
    for f in findings:
        center = _span_center(spans, f.start, f.end)
        if center is None:
            continue
        page_index, cx, cy = center
        if page_index >= len(sizes):
            continue
        width, height = sizes[page_index]
        current_positions.append((f.type, page_index, _bucket(cx, width), _bucket(cy, height)))

    suggestions: list[Finding] = []
    seen_spans: set[tuple[int, int]] = set()
    for item in matched["items"]:
        if _already_covered(item["type"], item["page_index"], item["x_bucket"], item["y_bucket"], current_positions):
            continue
        span = _nearest_span(spans, item["page_index"], item["x_bucket"], item["y_bucket"], sizes)
        if span is None:
            continue
        resolved = _value_after_label_in_span(span, item.get("label", ""))
        if resolved is None:
            continue
        start, end, value = resolved
        if (start, end) in seen_spans or (start, end) in {(f.start, f.end) for f in findings}:
            continue
        seen_spans.add((start, end))
        suggestions.append(Finding(
            item["type"], value, start, end,
            group="템플릿후보", confidence="낮음",
        ))
    return suggestions
