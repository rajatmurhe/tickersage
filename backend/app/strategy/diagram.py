"""A Mermaid flowchart of a spec's rules.

The output is canonical so the frontend can read it without a Mermaid runtime: one node per
line (`id["label"]`, `id{"label"}` for decisions, `id(["label"])` for exits), then one edge per
line (`a --> b` or `a -->|label| b`). Node ids avoid Mermaid's reserved `end`. Labels are built
from spec fields only, and each line is escaped with Mermaid's entity codes before lines are
joined with <br/>.
"""

from __future__ import annotations

from .spec import StrategySpec, bars_for_hours, describe, fmt_hours, fmt_num

_ENTITIES = {"#": "#35;", "&": "#amp;", '"': "#quot;", "<": "#lt;", ">": "#gt;", "|": "#124;", "[": "#91;",
             "]": "#93;", "{": "#123;", "}": "#125;"}


def _esc(line: str) -> str:
    return "".join(_ENTITIES.get(ch, ch) for ch in line)


def _label(*lines: str) -> str:
    return "<br/>".join(_esc(line) for line in lines if line)


def to_mermaid(spec: StrategySpec) -> str:
    sym = spec.symbol.upper()
    joiner = "all must hold" if spec.entry_logic == "all" or len(spec.entry) == 1 else "any one is enough"
    source = _label(f"{sym}/USDT {spec.interval} candles", "checked at each candle close")
    rule = _label(f"Entry rule ({joiner})", *(describe(c) for c in spec.entry))
    size = f"{fmt_num(spec.position_size_pct)}% of the ${spec.demo_balance:,.0f} demo balance"
    nodes: list[str] = [
        f'  candles["{source}"]',
        f'  entry{{"{rule}"}}',
        f'  buy["{_label("Buy at the next candle open", size)}"]',
        f'  hold{{"{_label("While holding, every candle")}"}}',
    ]
    edges = ["  candles --> entry", "  entry -->|no| candles", "  entry -->|yes| buy", "  buy --> hold"]
    exits: list[tuple[str, str, str]] = []  # (id, label, edge label)
    if spec.take_profit_pct is not None:
        exits.append(("tp", _label(f"Take profit at +{fmt_num(spec.take_profit_pct)}%", "sell at that price"),
                      _esc(f"high reaches +{fmt_num(spec.take_profit_pct)}%")))
    if spec.stop_loss_pct is not None:
        exits.append(("sl", _label(f"Stop loss at -{fmt_num(spec.stop_loss_pct)}%", "sell at that price"),
                      _esc(f"low reaches -{fmt_num(spec.stop_loss_pct)}%")))
    for i, c in enumerate(spec.exit_signals):
        exits.append((f"signal{i + 1}", _label("Exit signal", "sell at the next candle open"), _esc(describe(c))))
    if spec.max_hold_hours is not None:
        bars = bars_for_hours(spec.max_hold_hours, spec.interval)
        exits.append(("timeout", _label(f"Time exit after {fmt_hours(spec.max_hold_hours)}",
                                        "sell at the next candle open"),
                      _esc(f"{bars} candle{'s' if bars != 1 else ''} held")))
    exits.append(("eod", _label("End of data", "close at the last candle"), "data runs out"))
    for node_id, label, edge in exits:
        nodes.append(f'  {node_id}(["{label}"])')
        edges.append(f"  hold -->|{edge}| {node_id}")
    nodes.append(f'  flat["{_label("Flat: look for the next entry")}"]')
    edges += [f"  {node_id} --> flat" for node_id, _, _ in exits if node_id != "eod"]
    edges.append("  flat --> candles")
    return "\n".join(["flowchart TD", *nodes, *edges]) + "\n"
