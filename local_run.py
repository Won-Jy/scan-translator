"""노트북에서 바로 테스트할 때 쓰는 실행기 (Drive 없이).

사용법 (PowerShell):
    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    python local_run.py "C:\\경로\\스캔.pdf"
    python local_run.py "스캔.pdf" --pages 1-5      # 앞 5쪽만 테스트

결과: 같은 폴더에 <이름>_KO.pdf, 중간 결과는 _work\\<이름>.json
중간 결과가 있으면 끝난 페이지는 건너뛰므로, 중단돼도 다시 실행하면 이어집니다.
"""
import argparse
import json
import sys
from pathlib import Path

import pymupdf as fitz

from translator.glossary import glossary_to_text, parse_user_glossary
from translator.config import DEFAULT_GLOSSARY, ROOT
from translator.pipeline import run_pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="예: 1-5 (테스트용으로 일부만)")
    ap.add_argument("--glossary", default=str(ROOT / "glossary.txt"))
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    data = pdf_path.read_bytes()
    stem = pdf_path.stem
    if args.pages:
        a, b = (int(x) for x in args.pages.split("-"))
        src = fitz.open(stream=data, filetype="pdf")
        part = fitz.open()
        part.insert_pdf(src, from_page=a - 1, to_page=b - 1)
        data = part.tobytes()
        stem = f"{stem}_p{a}-{b}"

    gpath = Path(args.glossary)
    terms = parse_user_glossary(gpath.read_text(encoding="utf-8")) if gpath.exists() else dict(DEFAULT_GLOSSARY)

    work_dir = pdf_path.parent / "_work"
    work_dir.mkdir(exist_ok=True)
    work_file = work_dir / f"{stem}.json"
    work = json.loads(work_file.read_text(encoding="utf-8")) if work_file.exists() else {}

    def save(w):
        work_file.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")

    pdf, report = run_pipeline(data, work, terms, save=save)
    out = pdf_path.parent / f"{stem}_KO.pdf"
    out.write_bytes(pdf)
    (work_dir / f"{stem}_용어집.txt").write_text(glossary_to_text(work["glossary"]), encoding="utf-8")
    print("저장:", out)
    if report["failed_pages"]:
        print("원문으로 대체된 페이지:", report["failed_pages"])


if __name__ == "__main__":
    sys.exit(main())
