"""원고 분량 추정 — 마크다운 원고를 A4 2단 학회 양식에 가깝게 인쇄해 쪽수를 잰다.

**투고 양식이 아니다.** 여백·글꼴·줄 간격을 흔한 국내 학술대회 2단 양식에 맞춘 근사이고,
목적은 *"고친 뒤 몇 쪽인가"* 를 같은 자로 재는 것이다. 투고처 양식에 옮긴 뒤 다시 확인한다.

=====================  ===========================================
요소                    근사값
=====================  ===========================================
용지·여백               A4, 위 25 · 아래 22 · 좌우 18 mm
머리(제목·요약)         1단
본문                    2단(단 간격 6 mm), 명조, 줄 간격 1.45
표·코드·참고문헌        본문보다 1.5pt·1pt·1pt 작게
=====================  ===========================================

사용 (프로젝트 venv 를 건드리지 않는다):

    uv run --with markdown --with pypdf python docs/paper/measurePages.py \\
        docs/paper/draft-spring2027.md /tmp/draft.pdf --bodyPt 9

``# 아래는 원고가 아니다`` 아래(작성 메모)는 세지 않는다. Chrome 이 필요하다.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess

import markdown
import pypdf

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MEMO_MARKER = "# 아래는 원고가 아니다"
BODY_START = "## Ⅰ. 서론"
REFERENCES_START = "## 참고문헌"


def buildHtml(source: pathlib.Path, bodyPt: float) -> str:
    manuscript = source.read_text(encoding="utf-8").split(MEMO_MARKER)[0]
    manuscript = manuscript.rstrip().rstrip("-").rstrip()
    head, _, body = manuscript.partition(BODY_START)
    body, _, references = (BODY_START + body).partition(REFERENCES_START)
    extensions = ["tables", "fenced_code"]
    headHtml = markdown.markdown(head, extensions=extensions)
    bodyHtml = markdown.markdown(body, extensions=extensions)
    if references:
        bodyHtml += ('<div class="refs">'
                     + markdown.markdown(REFERENCES_START + references, extensions=extensions)
                     + "</div>")
    # 그림은 원고 위치 기준 상대 경로다
    base = source.resolve().parent
    bodyHtml = re.sub(r'src="([^":/][^"]*)"', lambda m: f'src="file://{base / m.group(1)}"', bodyHtml)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 25mm 18mm 22mm 18mm; }}
body {{ font-family: 'AppleMyungjo', 'Noto Serif CJK KR', serif; font-size: {bodyPt}pt;
        line-height: 1.45; text-align: justify; }}
.head h1 {{ font-size: {bodyPt + 6}pt; text-align: center; margin: 0 0 4pt; }}
.head h2 {{ font-size: {bodyPt + 1}pt; margin: 6pt 0 2pt; }}
.head p {{ margin: 3pt 0; }}
.head hr {{ display: none; }}
.cols {{ column-count: 2; column-gap: 6mm; }}
.cols h2 {{ font-size: {bodyPt + 1.5}pt; margin: 8pt 0 3pt; break-after: avoid; }}
.cols h3 {{ font-size: {bodyPt + 0.5}pt; margin: 6pt 0 2pt; break-after: avoid; }}
p {{ margin: 0 0 4pt; }}
table {{ border-collapse: collapse; width: 100%; font-size: {bodyPt - 1.5}pt; margin: 3pt 0;
         break-inside: avoid; }}
td, th {{ border: 0.5pt solid #444; padding: 1pt 2pt; }}
pre {{ font-size: {bodyPt - 1}pt; margin: 2pt 0; white-space: pre-wrap; }}
img {{ width: 100%; }}
ol, ul {{ margin: 0 0 4pt; padding-left: 14pt; }}
.refs p {{ font-size: {bodyPt - 1}pt; text-align: left; margin: 0 0 2pt; }}
</style></head><body><div class="head">{headHtml}</div><div class="cols">{bodyHtml}</div></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="원고 분량 추정 (A4 2단 근사)")
    parser.add_argument("source", type=pathlib.Path, help="마크다운 원고")
    parser.add_argument("output", type=pathlib.Path, help="만들 PDF 경로")
    parser.add_argument("--bodyPt", type=float, default=9.0, help="본문 글꼴 크기")
    arguments = parser.parse_args()

    output = arguments.output.resolve()
    htmlPath = output.with_suffix(".html")
    htmlPath.write_text(buildHtml(arguments.source, arguments.bodyPt), encoding="utf-8")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--allow-file-access-from-files", f"--print-to-pdf={output}",
                    f"file://{htmlPath}"], check=True, capture_output=True)
    pages = pypdf.PdfReader(str(output)).pages
    lastChars = len((pages[-1].extract_text() or "").strip())
    print(f"쪽수 {len(pages)} (본문 {arguments.bodyPt:g}pt) — 마지막 쪽 글자 {lastChars}")
    print(f"PDF: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
