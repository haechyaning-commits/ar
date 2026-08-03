"""
사전 자동 학습 (6.2.1).

검토자가 이미 하는 행동(체크 해제=제외 후보, 수동 이름 추가=성씨 후보)만으로
성씨사전/제외어사전을 조용히 개선. **확인 팝업 없이 진행하는 게 핵심 조건** --
"추가할까요?"가 아니라 "추가했습니다"로, 질문형이 아니라 통보형.

같은 (단어, 방향)이 임계값(기본 3회) 이상 반복돼야 실제 사전에 반영 -- 우연한
케이스 하나로 사전이 오염되는 걸 막기 위함. 반영 후에도 검토 화면에서 최근
항목은 "되돌리기" 버튼으로 되돌릴 수 있음 (review_ui.py가 get_recent_promotions로
목록을 보여주고 revert_promotion을 호출).

되돌리기를 "사전 텍스트 파일을 직접 열어 줄 삭제"로 요구하면 비개발자인
감사실 직원에게 비현실적이라, 검토 화면 안의 버튼 클릭으로 끝나게 함. 반영/취소
이력은 감사 도구 성격상 지우지 않고 이벤트 로그로 계속 쌓음(append-only).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).parent / "data"
CANDIDATES_FILENAME = "dictionary_candidates.csv"
LOG_FILENAME = "dictionary_learning_log.csv"

THRESHOLD = 3
RECENT_DAYS = 7
RECENT_LIMIT = 5

# 방향 -> 실제로 반영될 사전 파일. "제외"는 detector.EXCLUDE_WORDS가 정확히
# 일치하는 단어를 걸러내는 방식이라 값 그대로 사용 가능하지만, "이름후보"는
# SURNAMES가 성씨(대개 1글자)만 담고 있어 전체 이름이 아니라 성씨 자리에
# 해당하는 부분만 반영해야 함 -- 그 추출은 호출하는 쪽(review_ui.py) 책임.
CANDIDATE_DIRECTIONS = ("제외", "이름후보")
_DICT_FILENAME = {"제외": "exclude_words.txt", "이름후보": "surnames.txt"}

CANDIDATES_HEADER = ["단어", "방향", "발생일시"]
LOG_HEADER = ["단어", "방향", "이벤트", "일시"]  # 이벤트: 반영 | 취소


def _append_csv(path: Path, header: list[str], row: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(header)
        writer.writerow(row)


def _read_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    return rows[1:] if rows else []  # 헤더 제외


def _is_in_dictionary(word: str, direction: str, data_dir: Path) -> bool:
    dict_file = data_dir / _DICT_FILENAME[direction]
    if not dict_file.exists():
        return False
    with open(dict_file, encoding="utf-8") as f:
        return word in {line.strip() for line in f if line.strip()}


def record_candidate(word: str, direction: str, data_dir: Path = DEFAULT_DATA_DIR) -> bool:
    """검토자 행동 1건을 후보로 기록. 임계값(3회) 도달 시 실제 사전에 조용히
    반영하고 True(=방금 반영됨)를 반환, 아니면 False.

    확인을 구하지 않음 -- 호출하는 쪽은 반환값이 True일 때 통보 문구만 보여주면 됨.
    """
    if direction not in CANDIDATE_DIRECTIONS or not word:
        return False

    candidates_path = data_dir / CANDIDATES_FILENAME
    _append_csv(candidates_path, CANDIDATES_HEADER, [word, direction, datetime.now().isoformat()])

    if _is_in_dictionary(word, direction, data_dir):
        return False  # 이미 사전에 있음 -> 반영할 필요 없음

    count = sum(1 for w, d, _ in _read_rows(candidates_path) if w == word and d == direction)
    if count < THRESHOLD:
        return False

    dict_file = data_dir / _DICT_FILENAME[direction]
    with open(dict_file, "a", encoding="utf-8") as f:
        f.write(word + "\n")
    _append_csv(data_dir / LOG_FILENAME, LOG_HEADER, [word, direction, "반영", datetime.now().isoformat()])
    return True


@dataclass
class Promotion:
    word: str
    direction: str
    promoted_at: str


def get_recent_promotions(
    data_dir: Path = DEFAULT_DATA_DIR, within_days: int = RECENT_DAYS, limit: int = RECENT_LIMIT,
) -> list[Promotion]:
    """검토 화면에 '되돌리기' 목록으로 보여줄 최근 반영 내역.
    같은 (단어, 방향)의 가장 최근 이벤트가 '취소'면 이미 되돌려진 것이므로 제외."""
    rows = _read_rows(data_dir / LOG_FILENAME)
    latest: dict[tuple[str, str], tuple[str, str]] = {}
    for word, direction, event, ts in rows:
        key = (word, direction)
        if key not in latest or ts > latest[key][1]:
            latest[key] = (event, ts)

    cutoff = datetime.now() - timedelta(days=within_days)
    promotions = [
        Promotion(word, direction, ts)
        for (word, direction), (event, ts) in latest.items()
        if event == "반영" and datetime.fromisoformat(ts) >= cutoff
    ]
    promotions.sort(key=lambda p: p.promoted_at, reverse=True)
    return promotions[:limit]


def revert_promotion(word: str, direction: str, data_dir: Path = DEFAULT_DATA_DIR) -> None:
    """사전에서 단어를 빼고, 쌓여있던 후보 발생 기록도 초기화 (되돌린 직후
    바로 재반영되는 걸 막기 위함 -- 초기화 후 다시 3회 쌓여야 재반영됨)."""
    dict_file = data_dir / _DICT_FILENAME[direction]
    if dict_file.exists():
        with open(dict_file, encoding="utf-8") as f:
            lines = [line for line in f if line.strip() != word]
        with open(dict_file, "w", encoding="utf-8") as f:
            f.writelines(lines)

    _append_csv(data_dir / LOG_FILENAME, LOG_HEADER, [word, direction, "취소", datetime.now().isoformat()])

    candidates_path = data_dir / CANDIDATES_FILENAME
    remaining = [r for r in _read_rows(candidates_path) if not (r[0] == word and r[1] == direction)]
    with open(candidates_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CANDIDATES_HEADER)
        writer.writerows(remaining)
