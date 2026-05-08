import os
import subprocess
import sys

# Windows 콘솔 인코딩 에러 방지
sys.stdout.reconfigure(encoding='utf-8')

def run_profilers():
    keywords = ["버거", "김밥", "김치찌개", "샐러드", "아구찜", "조개", "족발", "청국장", "칼국수", "피자", "한상", "회"]
    
    print("🚀 전체 프로파일링 일괄 실행 시작...")
    
    for kw in keywords:
        print(f"\n{'='*50}")
        print(f"👉 [1/2] Food_profiler 실행 중: {kw}")
        print(f"{'='*50}")
        # env 설정
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'

        # Run Food_profiler (text)
        result1 = subprocess.run([sys.executable, "Food_profiler.py", kw], cwd=os.getcwd(), env=env)
        if result1.returncode != 0:
            print(f"❌ Food_profiler 실패: {kw}. 다음으로 넘어갑니다.")
            continue
            
        print(f"\n{'='*50}")
        print(f"👉 [2/2] Multimodal_profiler 실행 중: {kw}")
        print(f"{'='*50}")
        # Run Multimodal_profiler (image)
        result2 = subprocess.run([sys.executable, "Multimodal_profiler.py", kw], cwd=os.getcwd(), env=env)
        if result2.returncode != 0:
            print(f"❌ Multimodal_profiler 실패: {kw}")
        
    print("\n🎉 모든 키워드 프로파일링이 완료되었습니다!")

if __name__ == "__main__":
    run_profilers()
