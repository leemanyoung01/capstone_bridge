import db
import sys

def clean_up_db():
    print("Starting DB cleanup for profiler data...")
    # 지정된 12개의 키워드에 대한 프로파일링(분석) 결과만 삭제합니다.
    # 크롤링된 식당 정보나 실제 리뷰 텍스트는 하나도 지워지지 않습니다.
    keywords_to_clean = ["김밥", "김치찌개", "샐러드", "아구찜", "조개", "족발", "청국장", "칼국수", "피자", "한상", "회", "버거"]
    
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            # 1. Delete from restaurant_vectors
            cur.execute("DELETE FROM restaurant_vectors WHERE keyword = ANY(%s)", (keywords_to_clean,))
            print(f"Deleted {cur.rowcount} rows from restaurant_vectors.")
            
            # 2. Delete from representative_images
            cur.execute("DELETE FROM representative_images WHERE keyword = ANY(%s)", (keywords_to_clean,))
            print(f"Deleted {cur.rowcount} rows from representative_images.")
            
            # 3. Delete from axes_config
            cur.execute("DELETE FROM axes_config WHERE keyword = ANY(%s)", (keywords_to_clean,))
            print(f"Deleted {cur.rowcount} rows from axes_config.")

if __name__ == "__main__":
    clean_up_db()
    print("DB cleanup completed.")
