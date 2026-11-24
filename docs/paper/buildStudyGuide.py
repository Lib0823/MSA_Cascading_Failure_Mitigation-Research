"""학습용 해설서 빌드 — 마크다운 → HTML(A4) → Chrome headless 인쇄 → PDF.

[study-guide-spring2027.md](study-guide-spring2027.md) 를 고친 뒤 PDF 를 다시 만들 때 쓴다. 표지·차례(목차
링크)·쪽 번호·PDF 책갈피를 넣고, 부(`#`)마다 새 쪽에서 시작한다. 그림은 원고 위치 기준 상대 경로로 찾는다.

사용 (프로젝트 venv 를 건드리지 않는다):

    uv run --with markdown --with pypdf python docs/paper/buildStudyGuide.py \\
        docs/paper/study-guide-spring2027.md docs/paper/study-guide-spring2027.pdf

Chrome 이 필요하다. 원고는 Python-Markdown 문법이다 — 문단 바로 뒤의 목록에는 빈 줄, 중첩 목록은 4칸 들여쓰기.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import tempfile

import markdown
import pypdf
from markdown.extensions.toc import slugify_unicode

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page {
  size: A4;
  margin: 18mm 17mm 20mm 17mm;
  @bottom-center { content: counter(page); font-family: "Apple SD Gothic Neo", sans-serif;
                   font-size: 8.5pt; color: #8a919c; }
}
@page :first { @bottom-center { content: none; } }
html { font-family: "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif;
       font-size: 10pt; line-height: 1.65; color: #1d2430; }
body { margin: 0; word-break: keep-all; overflow-wrap: break-word; }
.cover { height: 240mm; display: flex; flex-direction: column; justify-content: center;
         padding: 0 0 0 10mm; border-left: 6pt solid #1f3a5f; }
.cover-kicker { font-size: 10pt; color: #5a6b80; margin: 0 0 12pt; }
.cover-title { font-size: 23pt; font-weight: 800; line-height: 1.38; color: #1f3a5f; margin: 0 0 16pt; }
.cover-sub { font-size: 12.5pt; color: #33475f; margin: 0 0 30pt; }
.cover-meta { font-size: 9pt; color: #5a6b80; line-height: 1.8; }
.toc-title { break-before: page; font-size: 17pt; font-weight: 800; color: #1f3a5f;
             border-bottom: 2pt solid #1f3a5f; padding-bottom: 4pt; margin: 0 0 10pt; }
.toc { line-height: 1.38; }
.toc ul { list-style: none; padding-left: 0; margin: 0; }
.toc > ul > li { margin: 5pt 0 1pt; font-weight: 700; font-size: 10.3pt; }
.toc > ul > li > ul { margin: 2pt 0 2pt 14pt; font-weight: 400; }
.toc > ul > li > ul > li { margin: 0; font-size: 9.2pt; }
.toc a { color: #1d2430; text-decoration: none; }
h1 { break-before: page; font-size: 18pt; font-weight: 800; color: #1f3a5f;
     border-bottom: 2pt solid #1f3a5f; padding-bottom: 4pt; margin: 0 0 12pt; }
h2 { font-size: 13pt; font-weight: 700; color: #1f3a5f; margin: 18pt 0 6pt; break-after: avoid; }
h3 { font-size: 11pt; font-weight: 700; color: #2c4f7c; margin: 13pt 0 4pt; break-after: avoid; }
p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 8pt; padding-left: 16pt; }
li { margin: 2pt 0; }
li > ul, li > ol { margin: 2pt 0 2pt; }
blockquote { margin: 6pt 0 10pt; padding: 7pt 11pt; border-left: 3pt solid #9fb3cc;
             background: #f6f8fb; color: #2a3544; }
blockquote p:last-child { margin-bottom: 0; }
blockquote { break-inside: avoid; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 12pt; font-size: 8.8pt; line-height: 1.5; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { background: #e9eef5; color: #1f3a5f; font-weight: 700; text-align: left; }
td, th { border: 0.6pt solid #c8d0db; padding: 3pt 5pt; vertical-align: top; }
pre { background: #f5f7fa; border: 0.6pt solid #dce3eb; border-radius: 4pt; padding: 7pt 9pt;
      margin: 6pt 0 10pt; font-family: Menlo, monospace; font-size: 8.3pt; line-height: 1.55;
      white-space: pre-wrap; break-inside: avoid; }
code { font-family: Menlo, monospace; font-size: 8.5pt; background: #eef1f5; padding: 0.5pt 3pt;
       border-radius: 2pt; overflow-wrap: anywhere; }
pre code { background: none; padding: 0; font-size: inherit; }
img { display: block; max-width: 100%; margin: 6pt auto 10pt; }
.box { border-left: 4pt solid; border-radius: 3pt; padding: 6pt 10pt 2pt; margin: 6pt 0 9pt;
       break-inside: avoid; }
.box-title { font-weight: 800; margin: 0 0 3pt; }
.box p { margin-bottom: 5pt; }
.box ol, .box ul { margin-bottom: 5pt; }
.key { border-color: #2f6fb3; background: #eef5fc; } .key .box-title { color: #1f5a99; }
.warn { border-color: #c9801b; background: #fdf5e7; } .warn .box-title { color: #9a5d0a; }
.qa { border-color: #2f8a57; background: #eef8f1; } .qa .box-title { color: #22683f; }
.lab { border-color: #7a4fb0; background: #f5f0fb; } .lab .box-title { color: #5d3a8c; }
strong { color: #10233d; }
"""


def buildHtml(source: pathlib.Path) -> str:
    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "md_in_html", "attr_list", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "1-2", "slugify": slugify_unicode}})
    body = converter.convert(source.read_text(encoding="utf-8"))
    # 그림은 원고 위치 기준 상대 경로다
    base = source.parent
    body = re.sub(r'src="([^":/][^"]*)"', lambda m: f'src="file://{base / m.group(1)}"', body)
    return (f'<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
            f'<title>논문 해설서 — 2027 춘계 학회 원고</title><style>{CSS}</style></head>'
            f'<body>{body}</body></html>')


def main() -> int:
    parser = argparse.ArgumentParser(description="학습용 해설서 PDF 빌드")
    parser.add_argument("source", type=pathlib.Path, help="마크다운 원고")
    parser.add_argument("output", type=pathlib.Path, help="만들 PDF 경로")
    arguments = parser.parse_args()

    source = arguments.source.resolve()
    output = arguments.output.resolve()
    # 중간 HTML 은 저장소에 남기지 않는다
    with tempfile.TemporaryDirectory() as workDir:
        htmlPath = pathlib.Path(workDir) / "study-guide.html"
        htmlPath.write_text(buildHtml(source), encoding="utf-8")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--generate-pdf-document-outline", "--allow-file-access-from-files",
                        f"--print-to-pdf={output}", f"file://{htmlPath}"], check=True, capture_output=True)
    reader = pypdf.PdfReader(str(output))
    print(f"쪽수 {len(reader.pages)} · 책갈피 {len(reader.outline)} · {output.stat().st_size / 1024:.0f} KB")
    print(f"PDF: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
