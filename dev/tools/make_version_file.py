"""PyInstaller 용 버전 정보 리소스 파일 생성 (build/version_info.txt).

Setup.exe 속성에 회사명·제품명·버전이 표시되도록 한다 — 리소스가 텅 빈
서명 없는 exe 는 Windows Defender ML 오탐(Sabsik 등)에 더 잘 걸린다.
version.py 의 __version__ 을 그대로 사용하므로 릴리스 시 따로 만질 것 없음.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # dev/tools/ → 프로젝트 루트
sys.path.insert(0, str(ROOT))
from version import __version__  # noqa: E402

m = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", __version__)
ver_tuple = tuple(int(m.group(i) or 0) for i in (1, 2, 3)) + (0,) if m else (0, 0, 0, 0)

CONTENT = f"""\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver_tuple},
    prodvers={ver_tuple},
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'RoofSkY'),
        StringStruct('FileDescription', '등하원차량등록 설치 프로그램'),
        StringStruct('FileVersion', '{__version__}'),
        StringStruct('InternalName', 'AnsimTalk-Setup'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 RoofSkY'),
        StringStruct('OriginalFilename', 'AnsimTalk-Setup.exe'),
        StringStruct('ProductName', '등하원차량등록 (auto-ansimtalk)'),
        StringStruct('ProductVersion', '{__version__}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

out = ROOT / "build" / "version_info.txt"
out.parent.mkdir(exist_ok=True)
out.write_text(CONTENT, encoding="utf-8")
print(f"생성: {out} (버전 {__version__} → {ver_tuple})")
