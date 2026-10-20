"""Report charts, built only from tool data: price (get_klines closes, rebased to 100 so assets at very different
prices share an axis) and volatility (compute_metrics, per candle and annualized).

The data goes to the frontend as JSON; the same data is drawn here as inline SVG for the HTML export, so the
exported file needs no scripts or network.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from ..schemas import ToolResult
from .models import Charts, PricePoint, PriceSeries, VolatilityBar
from .writing import first

PALETTE = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed"]


def build_charts(assets: list[str], by_asset: dict[str, list[ToolResult]], cites: dict[int, int]) -> Charts:
    charts = Charts()
    for a in assets:
        results = by_asset.get(a, [])
        k = first(results, "get_klines")
        candles = (k.data or {}).get("candles", []) if k else []
        if k and len(candles) >= 2 and candles[0]["close"]:
            base = candles[0]["close"]
            charts.price.append(PriceSeries(
                asset=a, interval=k.data["interval"], cite=cites.get(k.id),  # type: ignore[index]
                points=[PricePoint(t=c["open_time"], close=c["close"], index=round(c["close"] / base * 100, 2))
                        for c in candles],
            ))
        m = first(results, "compute_metrics")
        if m and m.data and m.data.get("volatility_pct") is not None:
            charts.volatility.append(VolatilityBar(
                asset=a, volatility_pct=m.data["volatility_pct"],
                annualized_volatility_pct=m.data["annualized_volatility_pct"], interval=m.data["interval"],
                cite=cites.get(m.id),
            ))
    return charts


# ----------------------------------------------------------------------- SVG

W, H = 640, 260
PAD_L, PAD_R, PAD_T, PAD_B = 48, 16, 16, 36


def _fmt_time(t: datetime, interval: str) -> str:
    return t.strftime("%H:%M" if interval in ("15m", "1h") else "%b %d")


def price_svg(series: list[PriceSeries]) -> str:
    if not series:
        return ""
    values = [p.index for s in series for p in s.points]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.08, 0.5)
    lo, hi = lo - pad, hi + pad
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def y(v: float) -> float:
        return PAD_T + ph * (1 - (v - lo) / (hi - lo))

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Price rebased to 100 at the window start" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif" font-size="11">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        out.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e5e7eb"/>')
        out.append(f'<text x="{PAD_L - 6}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#6b7280">{v:.1f}</text>')
    if lo < 100 < hi:
        out.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(100):.1f}" y2="{y(100):.1f}" stroke="#9ca3af" '
                   f'stroke-dasharray="4 3"/>')
    longest = max(series, key=lambda s: len(s.points))
    n = len(longest.points)
    for i in (0, n // 2, n - 1):
        x = PAD_L + pw * i / max(n - 1, 1)
        out.append(f'<text x="{x:.1f}" y="{H - PAD_B + 16}" text-anchor="middle" fill="#6b7280">'
                   f'{escape(_fmt_time(longest.points[i].t, longest.interval))}</text>')
    for si, s in enumerate(series):
        color = PALETTE[si % len(PALETTE)]
        m = len(s.points)
        pts = " ".join(f"{PAD_L + pw * i / max(m - 1, 1):.1f},{y(p.index):.1f}" for i, p in enumerate(s.points))
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/>')
        lx = PAD_L + 8 + si * 80
        out.append(f'<rect x="{lx}" y="{H - 14}" width="10" height="3" fill="{color}"/>')
        out.append(f'<text x="{lx + 14}" y="{H - 10}" fill="#374151">{escape(s.asset)}</text>')
    out.append("</svg>")
    return "".join(out)


def volatility_svg(bars: list[VolatilityBar]) -> str:
    if not bars:
        return ""
    top = max(b.annualized_volatility_pct for b in bars) * 1.15 or 1.0
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    slot = pw / len(bars)
    bw = min(64.0, slot * 0.5)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Annualized volatility by asset" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif" font-size="11">',
           f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{PAD_T + ph}" y2="{PAD_T + ph}" stroke="#9ca3af"/>']
    for i, b in enumerate(bars):
        h = ph * b.annualized_volatility_pct / top
        x = PAD_L + slot * i + (slot - bw) / 2
        yb = PAD_T + ph - h
        color = PALETTE[i % len(PALETTE)]
        out.append(f'<rect x="{x:.1f}" y="{yb:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{color}" rx="2"/>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{yb - 5:.1f}" text-anchor="middle" fill="#111827">'
                   f'{b.annualized_volatility_pct:.2f}%</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{PAD_T + ph + 16}" text-anchor="middle" fill="#374151">'
                   f'{escape(b.asset)} ({b.volatility_pct:.2f}% per {escape(b.interval)})</text>')
    out.append("</svg>")
    return "".join(out)
