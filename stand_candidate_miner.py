"""
Stand Candidate Miner — 리뷰에서 맛 표현 후보 자동 추출
========================================================
크롤링한 CSV 리뷰 텍스트를 분석하여
축 후보 키워드를 뽑고 candidate_terms.json으로 저장한다.

이 파일의 출력을 사람이 검수한 뒤 stand_builder.py로 Stand JSON을 생성.

실행: python stand_candidate_miner.py
      python stand_candidate_miner.py 짜장면
입력: 현재 폴더의 모든 CSV
출력: candidate_terms.json / candidate_terms.csv
"""

import pandas as pd
import re
import json
import os
import sys
import glob
from collections import Counter, defaultdict

# ── 맛/식감 관련 시드 패턴 ──
# 이 패턴에 걸리는 표현을 후보로 수집
TASTE_PATTERNS = [
    # 맛
    r"맛있|맛없|맛나|맛좋|존맛|꿀맛",
    r"매[운콤]|얼큰|칼칼|화끈|불닭",
    r"달[달콤]|단맛|스위트",
    r"짠|짭짤|싱거|밍밍",
    r"감칠|깊은.?맛|진한.?맛|풍미",
    r"고소|버터향|너티|견과",
    r"담백|깔끔|깨끗|슴슴",
    r"느끼|기름[지진]",
    r"시[원큼]|개운|청량",
    r"새콤|신맛|상큼",
    # 식감
    r"바삭|겉바|크리스피|파삭",
    r"촉촉|부드러|육즙|말랑",
    r"쫄깃|탱[글탱]|쫀득|찰떡",
    r"눅눅|퍽퍽|딱딱|푸석|무른|질기",
    # 온도
    r"뜨[거끈]|따[뜻끈]|식[어었]|미지근|차가",
    # 양/가격
    r"양이.?[많적]|푸짐|넉넉|배부|부족",
    r"가성비|저렴|비싸|착한.?가격",
    # 추천
    r"재주문|또.?시킬|단골|강추|추천|비추",
    # 음식 특화
    r"면[이발]|국물|소스|튀김|두[툼꺼]|얇|통째",
    r"불맛|웍향|숯불|직화",
    r"걸쭉|되직|묽",
    r"토핑|어묵|치즈|계란",
    r"육질|살살|녹[아는]|비계|지방",
    r"반찬|쌈|야채|사이드",
]


def load_all_csvs(keyword=None):
    """CSV 전체 로드, keyword가 있으면 필터링"""
    frames = []
    for f in sorted(glob.glob("*.csv")):
        try:
            tmp = pd.read_csv(f, encoding="utf-8-sig")
            if "Review" in tmp.columns:
                frames.append(tmp)
                print(f"  ✓ {f}: {len(tmp)}건")
        except:
            pass

    if not frames:
        print("  ERROR: CSV 없음!")
        return None

    df = pd.concat(frames, ignore_index=True)

    if keyword:
        mask = df.apply(
            lambda r: keyword.lower() in (str(r.get("Menu", "")) + " " + str(r.get("Review", ""))).lower(),
            axis=1,
        )
        df = df[mask].copy()
        print(f"  '{keyword}' 필터: {len(df)}건")

    return df


def extract_taste_expressions(texts):
    """리뷰 텍스트 리스트에서 맛/식감 표현 후보 추출"""
    pattern = "|".join(f"({p})" for p in TASTE_PATTERNS)
    compiled = re.compile(pattern)

    # 표현별 빈도
    term_counter = Counter()
    # 표현이 등장한 문맥 (예시 문장)
    term_examples = defaultdict(list)

    for text in texts:
        if not isinstance(text, str) or len(text) < 3:
            continue

        # 문장 단위로 분리
        sentences = re.split(r"[.!?\n]", text)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 3:
                continue

            matches = compiled.findall(sent)
            if not matches:
                continue

            # findall은 그룹별 튜플 반환 → 빈 문자열 제거
            for match_tuple in matches:
                for m in match_tuple:
                    if m:
                        term_counter[m] += 1
                        if len(term_examples[m]) < 3:
                            term_examples[m].append(sent[:80])

    return term_counter, term_examples


def extract_adjective_patterns(texts, min_count=3):
    """한국어 형용사/부사 패턴 추가 추출 (2글자 이상 형용사형 어미)"""
    adj_pattern = re.compile(r"(\w{2,5}[하한](?:다|고|게|지만|면서|는|ㄴ))")
    counter = Counter()

    for text in texts:
        if not isinstance(text, str):
            continue
        for m in adj_pattern.findall(text):
            counter[m] += 1

    # 빈도 낮은 것 제거 + 불용어 제거
    stopwords = {"합니다", "있다", "없다", "하다", "한다", "하고", "하는", "한데", "되다", "시키다"}
    return {k: v for k, v in counter.items() if v >= min_count and k not in stopwords}


def categorize_candidates(term_counter, term_examples):
    """후보 키워드를 대략적인 카테고리로 자동 분류 (사람 검수 전 초안)"""
    categories = {
        "맛_매운": ["매운", "매콤", "얼큰", "칼칼", "화끈", "불닭"],
        "맛_단":   ["달달", "달콤", "단맛", "스위트"],
        "맛_짠":   ["짠", "짭짤", "싱거", "밍밍"],
        "맛_감칠": ["감칠", "깊은", "진한", "풍미"],
        "맛_고소": ["고소", "버터", "너티"],
        "맛_담백": ["담백", "깔끔", "깨끗", "슴슴"],
        "맛_느끼": ["느끼", "기름지", "기름진"],
        "식감_바삭": ["바삭", "겉바", "크리스피", "파삭"],
        "식감_촉촉": ["촉촉", "부드러", "육즙", "말랑"],
        "식감_쫄깃": ["쫄깃", "탱글", "쫀득", "찰떡"],
        "온도":     ["뜨거", "뜨끈", "따뜻", "식어", "미지근"],
        "양":       ["양이", "푸짐", "넉넉", "배부", "부족"],
        "가격":     ["가성비", "저렴", "비싸", "착한"],
        "추천":     ["재주문", "단골", "강추", "추천", "비추"],
    }

    categorized = defaultdict(list)

    for term, count in term_counter.most_common():
        assigned = False
        for cat, seeds in categories.items():
            if any(s in term for s in seeds):
                categorized[cat].append({
                    "term": term,
                    "count": count,
                    "examples": term_examples.get(term, []),
                })
                assigned = True
                break
        if not assigned:
            categorized["미분류"].append({
                "term": term,
                "count": count,
                "examples": term_examples.get(term, []),
            })

    return dict(categorized)


def save_candidates(categorized, keyword=None):
    """JSON + CSV로 저장"""
    prefix = f"{keyword}_" if keyword else ""

    # JSON
    output = {
        "keyword": keyword or "전체",
        "total_terms": sum(len(v) for v in categorized.values()),
        "categories": categorized,
    }
    json_path = f"{prefix}candidate_terms.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # CSV (사람이 검수하기 편하게)
    rows = []
    for cat, terms in categorized.items():
        for t in terms:
            rows.append({
                "category": cat,
                "term": t["term"],
                "count": t["count"],
                "example1": t["examples"][0] if t["examples"] else "",
                "example2": t["examples"][1] if len(t["examples"]) > 1 else "",
                "keep": "",  # 사람이 O/X 표시할 열
                "assign_to_axis": "",  # 사람이 축 이름 적을 열
                "polarity": "",  # positive / negative
            })
    csv_path = f"{prefix}candidate_terms.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")

    print(f"\n  ✅ {json_path} ({output['total_terms']}개 후보)")
    print(f"  ✅ {csv_path} (사람 검수용)")
    return json_path, csv_path


def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else None

    print(f"\n{'━'*50}")
    print(f"  🔍 Stand Candidate Miner")
    if keyword:
        print(f"  키워드: '{keyword}'")
    print(f"{'━'*50}")

    # CSV 로드
    print(f"\n📂 CSV 로드...")
    df = load_all_csvs(keyword)
    if df is None:
        return

    # 리뷰 텍스트 추출
    texts = df["Review"].dropna().astype(str).tolist()
    print(f"  리뷰 {len(texts)}건 분석 중...")

    # 맛 표현 후보 추출
    term_counter, term_examples = extract_taste_expressions(texts)
    print(f"  패턴 매칭 후보: {len(term_counter)}개")

    # 카테고리 분류
    categorized = categorize_candidates(term_counter, term_examples)
    for cat, terms in sorted(categorized.items(), key=lambda x: -len(x[1])):
        top3 = ", ".join(t["term"] + f"({t['count']})" for t in terms[:3])
        print(f"    {cat:15s}: {len(terms)}개 — {top3}")

    # 저장
    save_candidates(categorized, keyword)

    print(f"\n{'━'*50}")
    print(f"  다음 단계:")
    print(f"  1. candidate_terms.csv를 열어서 keep 열에 O/X 표시")
    print(f"  2. assign_to_axis에 축 이름, polarity에 positive/negative 기입")
    print(f"  3. python stand_builder.py 실행하여 Stand JSON 생성")
    print(f"{'━'*50}")


if __name__ == "__main__":
    main()
