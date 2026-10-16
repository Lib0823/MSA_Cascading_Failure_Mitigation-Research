"""문서의 내부 링크가 실제 파일을 가리키는지 검사한다.

문서가 코드를 많이 참조하는 저장소라 **파일을 옮기면 조용히 끊긴다.** 실제로
`code/chain/loadGen.py` 를 참조하는 링크가 있었는데 그 파일은
`code/calibration/loadGen.py` 에 있었다.

오탐을 걸러야 쓸 수 있다
=========================

순진하게 `[...](...)` 를 잡으면 두 가지가 걸린다.

1. **수식** — `E[비용|a]` 뒤에 괄호가 오면 마크다운 링크처럼 보인다
2. **URL 인코딩** — 파일명에 `:` 나 공백이 있으면 `%3A`·`%20` 로 적는데,
   디코딩하지 않으면 없는 파일로 읽힌다
"""

from __future__ import annotations

import pathlib
import re
import sys
import urllib.parse

# 링크 텍스트가 이것으로 시작하면 수식이다. `E[비용|a](...)` 같은 형태.
FORMULA_PREFIXES = ("비용", "E[", "p_eff")
SKIP_SCHEMES = ("http://", "https://", "mailto:", "notion://")


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    broken, checked = [], 0
    for md in sorted(root.rglob("*.md")):
        if any(part in (".git", "node_modules", ".venv") for part in md.parts):
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r'\[([^\]]*)\]\(([^)#]+?)(?:#[^)]*)?\)', text):
            label, target = match.group(1).strip(), match.group(2).strip()
            if target.startswith(SKIP_SCHEMES):
                continue
            # 수식 오탐: 링크 텍스트가 파이프를 품거나 수식 접두어로 시작한다
            if "|" in label or label.startswith(FORMULA_PREFIXES):
                continue
            checked += 1
            # URL 인코딩을 풀어야 실제 파일명과 비교된다
            decoded = urllib.parse.unquote(target)
            if not (md.parent / decoded).resolve().exists():
                broken.append((md, target))

    print(f"내부 링크 {checked}개 검사")
    if not broken:
        print("✅ 깨진 링크 없음")
        return 0
    print(f"❌ 깨진 링크 {len(broken)}개")
    for path, target in broken:
        print(f"  {path}  →  {target}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
