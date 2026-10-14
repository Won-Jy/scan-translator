"""4단계: 번역 PDF 조판 — 원문과 1:1 페이지 대응.

원문 n쪽 = 번역본 n쪽. 글이 넘치면 그 페이지만 글자를 조금 줄여서 맞춘다.
참고문헌·색인 페이지는 원문 페이지를 그대로 넣고,
그림 페이지는 원문 그림 아래에 번역 캡션을 붙인다.
"""
import html
import re
from pathlib import Path

import pymupdf as fitz
import requests

from . import config


# ── 폰트 ─────────────────────────────────────────────
def ensure_fonts() -> Path:
    config.FONT_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in config.FONT_URLS.items():
        p = config.FONT_DIR / name
        if not p.exists() or p.stat().st_size < 100_000:
            print(f"폰트 다운로드: {name}")
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            p.write_bytes(r.content)
    return config.FONT_DIR


# ── 텍스트 → HTML ────────────────────────────────────
_ALLOWED = re.compile(r"&lt;(/?)(i|b|sup)&gt;", re.I)


def to_html(text: str) -> str:
    t = text or ""
    t = re.sub(r"[\x00-\x08\x0B-\x1F\x7F​﻿]", "", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)  # 혹시 남은 마크다운
    t = html.escape(t, quote=False)
    t = _ALLOWED.sub(lambda m: f"<{m.group(1)}{m.group(2).lower()}>", t)
    t = re.sub(r"&lt;br\s*/?&gt;", "<br>", t, flags=re.I)
    return t.replace("\n", "<br>")


def strip_tags(text: str) -> str:
    t = re.sub(r"<br\s*/?>|\n", " ", text or "", flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t)).strip()


def _css(scale: float, page_h: float) -> str:
    fs = config.BASE_FONT_PT * scale
    return f"""
@font-face {{ font-family: kr; src: url({config.FONT_REGULAR}); }}
@font-face {{ font-family: kr; src: url({config.FONT_BOLD}); font-weight: bold; }}
* {{ margin: 0; padding: 0; }}
body {{ font-family: kr; font-size: {fs:.2f}pt; line-height: {config.LINE_HEIGHT}; color: #1a1a1a;
        text-align: justify; }}
p {{ margin-bottom: 0.38em; text-indent: 1em; }}
p.cont, p.noind {{ text-indent: 0; }}
i {{ font-style: normal; color: #8a3a12; }}
b {{ font-weight: bold; }}
sup {{ font-size: 0.68em; color: #555; }}
h1 {{ font-size: 1.32em; font-weight: bold; text-align: center; line-height: 1.45;
      margin: 0.4em 0 1.1em 0; }}
h2 {{ font-size: 1.14em; font-weight: bold; text-align: left; line-height: 1.45;
      margin: 0.9em 0 0.6em 0; }}
h3 {{ font-size: 1.03em; font-weight: bold; text-align: left; margin: 0.7em 0 0.4em 0; }}
.bq {{ margin: 0.5em 0 0.7em 1.6em; font-size: 0.93em; }}
.bq p {{ text-indent: 0; }}
.epi {{ margin: 0.4em 0 1.2em 30%; font-size: 0.9em; text-align: left; }}
.epi p {{ text-indent: 0; }}
p.li {{ text-indent: -1em; margin-left: 1em; margin-bottom: 0.25em; text-align: left; }}
p.cap {{ text-indent: 0; font-size: 0.86em; color: #333; text-align: center; }}
p.tr {{ text-indent: 0; font-size: 0.9em; text-align: left; margin-bottom: 0.15em; }}
.opening {{ padding-top: {page_h * 0.10:.0f}pt; }}
.fnbox {{ border-top: 0.5pt solid #999; padding-top: 0.35em; }}
p.fn {{ font-size: 0.8em; line-height: 1.5; text-indent: 0; margin-bottom: 0.2em; }}
.fnm {{ color: #555; }}
"""


def _body_html(blocks: list[dict], texts: list[str], page: dict) -> str:
    out = []
    first_para_done = False
    i = 0
    while i < len(blocks):
        b, t = blocks[i], texts[i]
        typ = b["type"]
        if typ == "footnote" or not t.strip():
            i += 1
            continue
        h = to_html(t)
        if typ == "heading":
            lvl = b.get("level", 2)
            out.append(f"<h{lvl}>{h}</h{lvl}>")
        elif typ in ("blockquote", "epigraph"):
            cls = "bq" if typ == "blockquote" else "epi"
            out.append(f'<div class="{cls}"><p>{h}</p></div>')
        elif typ == "list_item":
            out.append(f'<p class="li">{h}</p>')
        elif typ == "caption":
            out.append(f'<p class="cap">{h}</p>')
        elif typ == "table_row":
            out.append(f'<p class="tr">{h.replace(" | ", "　│　")}</p>')
        elif typ == "other":
            out.append(f'<p class="noind">{h}</p>')
        else:  # paragraph
            cont = (not first_para_done and i == 0 and page.get("continues_paragraph"))
            out.append(f'<p class="cont">{h}</p>' if cont else f"<p>{h}</p>")
        if typ == "paragraph":
            first_para_done = True
        i += 1
    body = "\n".join(out)
    if page.get("page_kind") == "chapter_opening":
        body = f'<div class="opening">{body}</div>'
    return body


def _footnote_html(blocks: list[dict], texts: list[str]) -> str:
    items = []
    for b, t in zip(blocks, texts):
        if b["type"] == "footnote" and t.strip():
            m = to_html(b.get("marker", ""))
            items.append(f'<p class="fn"><span class="fnm">{m}</span> {to_html(t)}</p>' if m
                         else f'<p class="fn">{to_html(t)}</p>')
    return f'<div class="fnbox">{"".join(items)}</div>' if items else ""


# ── 맞춤 배치 ────────────────────────────────────────
class _Fitter:
    """임시 문서에 먼저 넣어 보고 맞는 글자 크기를 찾는다."""

    def __init__(self, archive: fitz.Archive):
        self.archive = archive
        self.scratch = fitz.open()

    def _try(self, rect: fitz.Rect, html_s: str, css: str) -> float:
        pg = self.scratch.new_page(width=rect.x1 + 10, height=rect.y1 + 10)
        spare, _ = pg.insert_htmlbox(rect, html_s, css=css, archive=self.archive, scale_low=1)
        self.scratch.delete_page(-1)
        return spare

    def height_needed(self, width: float, html_s: str, css: str) -> float:
        tall = 3000
        spare = self._try(fitz.Rect(0, 0, width, tall), html_s, css)
        return tall if spare < 0 else tall - spare

    def fits(self, rect: fitz.Rect, html_s: str, css: str) -> bool:
        return self._try(rect, html_s, css) >= 0


def _scales():
    s = 1.0
    while s >= config.MIN_SCALE - 1e-6:
        yield round(s, 3)
        s -= 0.04
    yield from (0.56, 0.5, 0.45, 0.4)


def _place_text(page, fitter, archive, rect, body, fns, page_h) -> float:
    gap = 8
    for s in _scales():
        css = _css(s, page_h)
        fn_h = fitter.height_needed(rect.width, fns, css) if fns else 0
        body_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y1 - fn_h - (gap if fns else 0))
        if body_rect.height < 40:
            continue
        if not body.strip() or fitter.fits(body_rect, body, css):
            if body.strip():
                page.insert_htmlbox(body_rect, body, css=css, archive=archive, scale_low=1)
            if fns:
                page.insert_htmlbox(fitz.Rect(rect.x0, rect.y1 - fn_h, rect.x1, rect.y1),
                                    fns, css=css, archive=archive, scale_low=1)
            return s
    # 최후의 수단: 한 상자에 몰아넣고 MuPDF가 알아서 축소
    css = _css(0.4, page_h)
    page.insert_htmlbox(rect, body + fns, css=css, archive=archive, scale_low=0)
    return 0.0


def _footer(page, archive, label: str, page_w: float, page_h: float):
    if not label:
        return
    css = _css(0.75, page_h) + "p{ text-align:center; text-indent:0; color:#888; }"
    r = fitz.Rect(0, page_h - config.MARGIN_BOTTOM + 12, page_w, page_h - 6)
    page.insert_htmlbox(r, f"<p>{html.escape(label)}</p>", css=css, archive=archive, scale_low=0)


# ── 메인 ─────────────────────────────────────────────
def render_book(src: fitz.Document, pages: list[dict], translations: list, book: dict) -> bytes:
    font_dir = ensure_fonts()
    archive = fitz.Archive(str(font_dir))
    fitter = _Fitter(archive)
    out = fitz.open()
    toc: list[list] = []
    shrunk = []

    for i in range(src.page_count):
        sr = src[i].rect
        page_w = config.PAGE_WIDTH
        page_h = round(page_w * sr.height / sr.width, 1)
        page = out.new_page(width=page_w, height=page_h)
        p = pages[i] if i < len(pages) else {"page_kind": "blank", "blocks": []}
        texts = translations[i] if i < len(translations) else None
        kind = p.get("page_kind", "body")
        content = fitz.Rect(config.MARGIN_X, config.MARGIN_TOP,
                            page_w - config.MARGIN_X, page_h - config.MARGIN_BOTTOM)

        label = p.get("printed_page_number", "")
        _footer(page, archive, label, page_w, page_h)

        if kind == "blank":
            continue
        if kind in ("bibliography", "index") or texts is None:
            # 원문 그대로 (서지는 번역하지 않음 / 번역 실패 시 내용 보존)
            page.show_pdf_page(page.rect, src, i, keep_proportion=True)
            continue

        blocks = p["blocks"]
        body = _body_html(blocks, texts, p)
        fns = _footnote_html(blocks, texts)

        if kind == "figure" or (p.get("has_figure") and len(strip_tags(body)) < 400):
            # 위: 원문 페이지(그림) / 아래: 번역된 캡션·짧은 글
            css = _css(0.92, page_h)
            need = fitter.height_needed(content.width, body + fns, css) if (body + fns).strip() else 0
            text_h = min(need, content.height * 0.45)
            img_rect = fitz.Rect(content.x0, content.y0, content.x1, content.y1 - text_h - (6 if text_h else 0))
            page.show_pdf_page(img_rect, src, i, keep_proportion=True)
            if text_h:
                txt_rect = fitz.Rect(content.x0, content.y1 - text_h, content.x1, content.y1)
                page.insert_htmlbox(txt_rect, body + fns, css=css, archive=archive, scale_low=0)
        else:
            s = _place_text(page, fitter, archive, content, body, fns, page_h)
            if s < 0.999:
                shrunk.append((i + 1, s))

        for b, t in zip(blocks, texts):
            if b["type"] == "heading" and b.get("level", 2) <= 2 and strip_tags(t):
                toc.append([b.get("level", 2), strip_tags(t)[:80], i + 1])

    if shrunk:
        print("  글자 축소된 페이지:", ", ".join(f"{n}쪽({s:.0%})" for n, s in shrunk))

    # PDF 목차(북마크) — 레벨이 건너뛰지 않도록 보정
    fixed, prev = [], 0
    for lvl, title, pno in toc:
        lvl = min(lvl, prev + 1)
        fixed.append([lvl, title, pno])
        prev = lvl
    if fixed:
        out.set_toc(fixed)

    title = book.get("title") or ""
    out.set_metadata({
        "title": f"{title} (한국어 번역)" if title else "한국어 번역",
        "author": book.get("author") or "",
        "creator": "scan-translator",
    })
    out.subset_fonts()
    data = out.tobytes(garbage=4, deflate=True)
    out.close()
    return data
