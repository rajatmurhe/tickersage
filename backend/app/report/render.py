"""The finished report as Markdown (what the verifier checks and the UI shows) and as a standalone HTML file.

The HTML export escapes every piece of text before adding the little markup the report uses (bold, lists,
tables, citation markers), so a headline or model output can't inject markup. It has no scripts and no
external requests; print it from the browser to get a PDF.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape

from ..schemas import Citation
from ..symbols import asset_name
from .charts import price_svg, volatility_svg
from .models import Charts

WINDOW_TITLES = {"24h": "Daily", "7d": "Weekly", "30d": "Monthly"}
WINDOW_LABELS = {"24h": "last 24 hours", "7d": "last 7 days", "30d": "last 30 days"}
DISCLAIMER = "Market research from public data only. This is not investment advice."


def title_for(window: str, assets: list[str]) -> str:
    names = assets[0] if len(assets) == 1 else ", ".join(assets[:-1]) + " and " + assets[-1]
    return f"{WINDOW_TITLES[window]} research report: {names}"


def subtitle(window: str, as_of: datetime | None, sources: list[str]) -> str:
    when = f" · data as of {as_of:%Y-%m-%d %H:%M} UTC" if as_of else ""
    src = f" · {', '.join(dict.fromkeys(sources))}" if sources else ""
    return f"Window: {WINDOW_LABELS[window]}{when}{src}"


def body_markdown(summary: str, sections: list[tuple[str, str]], comparison: str | None, risks: list[str]) -> str:
    """The verified part of the report: summary, per-asset sections, comparison and risks."""
    parts = ["## Summary", summary]
    for asset, text in sections:
        name = asset_name(asset)
        parts += [f"## {name} ({asset})" if name != asset else f"## {asset}", text]
    if comparison:
        parts += ["## Comparison", comparison]
    parts += ["## Risks and caveats", "\n".join(f"- {r}" for r in risks)]
    return "\n\n".join(parts)


def sources_markdown(citations: list[Citation]) -> str:
    lines = [f"{c.id}. {c.title}" + (f" ({c.url})" if c.url else "") for c in citations]
    return "## Sources\n\n" + ("\n".join(lines) if lines else "No sources: no data could be fetched.")


def full_markdown(title: str, sub: str, body: str, citations: list[Citation], denial: str | None) -> str:
    parts = [f"# {title}", f"_{sub}_", body]
    if denial:
        parts.append(denial)
    parts += [sources_markdown(citations), f"_{DISCLAIMER}_"]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------- HTML

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])_(.+?)_(?![\w*])")
_CITE = re.compile(r"\[(\d+(?:, \d+)*)\]")


def _inline(text: str) -> str:
    t = escape(text, quote=False)
    t = _BOLD.sub(r"<strong>\1</strong>", t)
    t = _ITALIC.sub(r"<em>\1</em>", t)
    return _CITE.sub(lambda m: "<sup>[" + ", ".join(f'<a href="#src-{n}">{n}</a>' for n in m.group(1).split(", "))
                     + "]</sup>", t)


def _table(lines: list[str]) -> str:
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in lines]
    head, body = rows[0], [r for r in rows[2:]]
    out = ["<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
    out += ["<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body]
    return "".join(out) + "</tbody></table>"


def md_to_html(md: str) -> str:
    """The Markdown subset the report uses: headings, paragraphs, bullet lists, pipe tables, bold, italics."""
    out: list[str] = []
    for block in re.split(r"\n{2,}", md.strip()):
        lines = block.splitlines()
        if block.startswith("## "):
            out.append(f"<h2>{_inline(block[3:])}</h2>")
        elif block.startswith("# "):
            out.append(f"<h1>{_inline(block[2:])}</h1>")
        elif all(line.lstrip().startswith("|") for line in lines) and len(lines) >= 2:
            out.append(_table(lines))
        elif all(line.startswith("- ") for line in lines):
            out.append("<ul>" + "".join(f"<li>{_inline(line[2:])}</li>" for line in lines) + "</ul>")
        elif lines[0].startswith("- ") and len(lines) > 1:  # a lead-in line followed by bullets
            out.append("<ul>" + "".join(f"<li>{_inline(line[2:])}</li>" for line in lines if line.startswith("- "))
                       + "</ul>")
        elif not lines[0].startswith("- ") and all(line.startswith("- ") for line in lines[1:]) and len(lines) > 1:
            out.append(f"<p>{_inline(lines[0])}</p><ul>"
                       + "".join(f"<li>{_inline(line[2:])}</li>" for line in lines[1:]) + "</ul>")
        else:
            out.append(f"<p>{'<br>'.join(_inline(line) for line in lines)}</p>")
    return "\n".join(out)


CSS = """
:root{color-scheme:light}
body{font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;color:#111827;background:#fff;max-width:820px;
margin:32px auto;padding:0 16px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 8px;border-bottom:1px solid #e5e7eb;padding-bottom:4px}
.sub{color:#6b7280;margin:0 0 16px}table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{border:1px solid #e5e7eb;padding:4px 8px;text-align:left}th{background:#f9fafb}
figure{margin:12px 0}figcaption{color:#6b7280;font-size:12px}svg{width:100%;height:auto}
sup a{color:#2563eb;text-decoration:none}ol.sources{font-size:13px;color:#374151}.disclaimer{color:#6b7280;font-size:13px}
.charts{display:grid;gap:8px}@media print{body{margin:0}a{color:inherit}}
"""


def report_html(title: str, sub: str, body: str, charts: Charts, citations: list[Citation], denial: str | None) -> str:
    figs = []
    if charts.price:
        cites = sorted({s.cite for s in charts.price if s.cite})
        figs.append(f"<figure>{price_svg(charts.price)}<figcaption>Close price rebased to 100 at the start of the "
                    f"window, from exchange candles{_cites(cites)}.</figcaption></figure>")
    if charts.volatility:
        cites = sorted({b.cite for b in charts.volatility if b.cite})
        figs.append(f"<figure>{volatility_svg(charts.volatility)}<figcaption>Annualized volatility, with the per-"
                    f"candle figure under each bar{_cites(cites)}.</figcaption></figure>")
    sources = "".join(
        f'<li id="src-{c.id}">{escape(c.title)}'
        + (f' (<a href="{escape(c.url)}" rel="noopener noreferrer">{escape(c.url)}</a>)' if c.url else "") + "</li>"
        for c in citations
    )
    body_html = md_to_html(body)
    charts_html = f'<h2>Charts</h2><div class="charts">{"".join(figs)}</div>' if figs else ""
    # Charts go after the summary.
    if charts_html and "</h2>" in body_html:
        first_section = body_html.find("<h2>", body_html.find("</h2>"))
        body_html = (body_html[:first_section] + charts_html + "\n" + body_html[first_section:]
                     if first_section > 0 else body_html + charts_html)
    denial_html = md_to_html(denial) if denial else ""
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>'
        f"<style>{CSS}</style></head><body><h1>{escape(title)}</h1><p class=\"sub\">{escape(sub)}</p>"
        f"{body_html}{denial_html}<h2>Sources</h2><ol class=\"sources\">{sources}</ol>"
        f'<p class="disclaimer">{escape(DISCLAIMER)}</p></body></html>'
    )


def _cites(ns: list[int]) -> str:
    return " " + _inline("[" + ", ".join(map(str, ns)) + "]") if ns else ""
