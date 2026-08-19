"""앱 진입점 — 실제 구현은 src/app.py 에 있다.

이 런처를 앱 루트에 남겨두는 이유:
  - 기존 설치본의 바로가기·자동 실행 등록이 `server.py` 를 직접 실행한다
  - updater 가 재시작할 때, installer 가 zip 의 앱 루트를 판정할 때도 이 파일을 찾는다
따라서 파일명/위치를 바꾸면 이미 배포된 PC 들이 실행되지 않는다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from app import main  # noqa: E402

if __name__ == "__main__":
    main()
