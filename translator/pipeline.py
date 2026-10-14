"""전체 흐름: 전사 → 용어집 → 번역 → 조판.

work(dict)에 중간 결과를 쌓아 두므로, 도중에 실패해도 다시 실행하면
끝난 페이지는 건너뛰고 이어서 처리한다.
한두 페이지가 끝내 실패해도 책 전체는 완성되고, 그 페이지에는 원문이 들어간다.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import pymupdf as fitz

from . import config
from .glossary import build_glossary, glossary_for_prompt
from .render import render_book
from .transcribe import render_page_jpeg, transcribe_page
from .translate import build_system, translate_page

WORK_VERSION = 2
NO_TRANSLATE = {"blank", "bibliography", "index", "failed"}
FAILED_PAGE = {"page_kind": "failed", "printed_page_number": "", "continues_paragraph": False,
               "starts_mid_sentence": False, "carryover_text": "", "ends_mid_sentence": False,
               "has_figure": False, "blocks": []}


def _context_tail(p: dict, n: int = 2) -> str:
    body = [b["text"] for b in p.get("blocks", []) if b["type"] != "footnote"]
    return "\n".join(body[-n:])[-700:]


def _run_parallel(items, fn, store, label, log, checkpoint) -> list[int]:
    """fn(i)를 병렬 실행해 store[str(i)]에 저장. 실패한 i 목록을 반환."""
    failed, done = [], 0
    with ThreadPoolExecutor(config.WORKERS) as ex:
        futs = {ex.submit(fn, i): i for i in items}
        for f in as_completed(futs):
            i = futs[f]
            try:
                store[str(i)] = f.result()
            except Exception as e:  # noqa: BLE001
                failed.append(i)
                log(f"  [실패] {label} {i + 1}쪽: {e}")
            done += 1
            if done % 10 == 0:
                log(f"  {label} {done}/{len(items)}")
                checkpoint()
    checkpoint()
    return sorted(failed)


def run_pipeline(
    pdf_bytes: bytes,
    work: dict,
    user_terms: dict[str, str],
    save: Callable[[dict], None] = lambda w: None,
    log: Callable[[str], None] = print,
) -> tuple[bytes, dict]:
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    n = src.page_count
    if work.get("version") != WORK_VERSION or work.get("page_count") != n:
        work.clear()
        work.update({"version": WORK_VERSION, "page_count": n, "pages": {}, "translations": {}})
    lock = threading.Lock()
    t0 = time.time()

    def checkpoint():
        with lock:
            save(work)

    # ── 1. 전사 ──────────────────────────────
    todo = [i for i in range(n) if str(i) not in work["pages"]]
    log(f"[1/4] 페이지 전사: {n}쪽 중 {len(todo)}쪽 처리")
    images = {i: render_page_jpeg(src[i]) for i in todo}
    failed_ocr = _run_parallel(todo, lambda i: transcribe_page(images[i], i + 1, n),
                               work["pages"], "전사", log, checkpoint)
    images.clear()
    if len(failed_ocr) > max(3, n // 5):
        raise RuntimeError(f"전사 실패 페이지가 너무 많습니다: {[i + 1 for i in failed_ocr]}")
    pages = [work["pages"].get(str(i), FAILED_PAGE) for i in range(n)]

    # ── 2. 용어집 ────────────────────────────
    if "glossary" not in work or work.get("user_terms") != user_terms:
        log("[2/4] 책 전체 용어집 생성")
        work["glossary"] = build_glossary(pages, user_terms)
        work["user_terms"] = user_terms
        work["translations"] = {}  # 용어가 바뀌면 번역도 다시
        checkpoint()
    else:
        log("[2/4] 용어집: 저장된 것 사용")
    g = work["glossary"]
    log(f"  『{g.get('title') or '?'}』 {g.get('author') or ''} — 용어 {len(g.get('terms', []))}개")
    system = build_system(glossary_for_prompt(g))

    # ── 3. 번역 ──────────────────────────────
    def job(i: int) -> list[str]:
        p = pages[i]
        prev = pages[i - 1] if i > 0 else None
        nxt = pages[i + 1] if i + 1 < n else None
        prev_ok = prev is not None and prev["page_kind"] not in NO_TRANSLATE
        next_ok = nxt is not None and nxt["page_kind"] not in NO_TRANSLATE
        skip = p["carryover_text"] if (p["starts_mid_sentence"] and prev_ok) else ""
        append = ""
        if p["ends_mid_sentence"] and next_ok and nxt["starts_mid_sentence"] and nxt["carryover_text"]:
            append = nxt["carryover_text"]
        ctx = _context_tail(prev) if prev_ok else ""
        return translate_page(system, p, skip_prefix=skip, append_suffix=append, prev_context=ctx)

    todo = [i for i in range(n)
            if str(i) not in work["translations"]
            and pages[i]["page_kind"] not in NO_TRANSLATE and pages[i]["blocks"]]
    log(f"[3/4] 번역: {len(todo)}쪽")
    failed_tr = []
    if todo:
        # 첫 페이지를 먼저 처리해 프롬프트 캐시를 데운 뒤 나머지를 병렬로
        failed_tr += _run_parallel(todo[:1], job, work["translations"], "번역", log, lambda: None)
        failed_tr += _run_parallel(todo[1:], job, work["translations"], "번역", log, checkpoint)
    if len(failed_tr) > max(3, len(todo) // 5):
        # API 크레딧 소진 등 — 원문투성이 PDF를 내보내지 말고 중단 (재실행하면 이어서 처리)
        raise RuntimeError(f"번역 실패 페이지가 너무 많습니다: {[i + 1 for i in failed_tr]}")

    # ── 4. 조판 ──────────────────────────────
    log("[4/4] PDF 조판")
    translations = []
    for i in range(n):
        p = pages[i]
        t = work["translations"].get(str(i))
        if p["page_kind"] == "failed" or (t is None and p["page_kind"] not in NO_TRANSLATE and p["blocks"]):
            translations.append(None)       # 원문 페이지로 대체
        else:
            translations.append(t or [""] * len(p["blocks"]))
    out = render_book(src, pages, translations, g)
    src.close()

    report = {
        "pages": n,
        "seconds": round(time.time() - t0),
        "failed_pages": sorted({i + 1 for i in failed_ocr + failed_tr}),
        "title": g.get("title", ""),
        "author": g.get("author", ""),
    }
    log(f"완료: {n}쪽, {report['seconds']}초"
        + (f" — 원문으로 대체된 페이지: {report['failed_pages']}" if report["failed_pages"] else ""))
    return out, report
