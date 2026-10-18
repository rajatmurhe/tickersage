import type { ReactNode } from "react";

/**
 * The small markdown subset the synthesizer and templates produce: paragraphs, **bold**, _italic_,
 * "-" lists, pipe tables and [n] citation markers. It builds React elements, never HTML strings,
 * so model and tool text (news headlines included) is always rendered as plain text.
 */

interface Props {
  text: string;
  /** Citation ids that exist in the answer's source list, linked to `#${anchor}-${id}`. */
  citationIds?: Set<number>;
  anchor?: string;
}

interface Ctx {
  citationIds?: Set<number>;
  anchor?: string;
}

const INLINE = /\*\*(.+?)\*\*|(^|\s)_(.+?)_(?=\s|$|[.,])|\[(\d+)\]/g;
const TABLE_ROW = /^\|.*\|$/;
const TABLE_RULE = /^\|[-| :]+\|$/;
const BULLET = /^\s*[-*] /;

export default function Markdown({ text, citationIds, anchor }: Props) {
  const ctx = { citationIds, anchor };
  const blocks = text.trim().split(/\n{2,}/).filter(Boolean);
  return <>{blocks.map((block, i) => renderBlock(block, ctx, i))}</>;
}

function renderBlock(block: string, ctx: Ctx, key: number): ReactNode {
  const lines = block.split("\n");
  if (lines.length >= 2 && TABLE_ROW.test(lines[0].trim()) && TABLE_RULE.test(lines[1].trim())) {
    const cells = (line: string) => line.trim().slice(1, -1).split("|").map((c) => c.trim());
    return (
      <div className="table-wrap" key={key}>
        <table>
          <thead>
            <tr>
              {cells(lines[0]).map((c, j) => (
                <th key={j}>{inline(c, ctx)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lines.slice(2).filter((l) => TABLE_ROW.test(l.trim())).map((l, r) => (
              <tr key={r}>
                {cells(l).map((c, j) => (
                  <td key={j}>{inline(c, ctx)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const listStart = lines.findIndex((l) => BULLET.test(l));
  if (listStart >= 0) {
    const before = lines.slice(0, listStart).join(" ").trim();
    const items: string[] = [];
    for (const l of lines.slice(listStart)) {
      if (BULLET.test(l)) items.push(l.replace(BULLET, ""));
      else items[items.length - 1] += ` ${l.trim()}`; // wrapped continuation line
    }
    return (
      <div key={key}>
        {before && <p>{inline(before, ctx)}</p>}
        <ul>
          {items.map((item, j) => (
            <li key={j}>{inline(item, ctx)}</li>
          ))}
        </ul>
      </div>
    );
  }

  return <p key={key}>{inline(lines.join(" "), ctx)}</p>;
}

function inline(text: string, ctx: Ctx): ReactNode[] {
  const out: ReactNode[] = [];
  const re = new RegExp(INLINE.source, "g");
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    // Keys are the element's index in `out`, which is unique.
    if (m[1] !== undefined) {
      out.push(<strong key={out.length}>{inline(m[1], ctx)}</strong>);
    } else if (m[3] !== undefined) {
      if (m[2]) out.push(m[2]);
      out.push(<em key={out.length}>{inline(m[3], ctx)}</em>);
    } else {
      const id = Number(m[4]);
      out.push(
        <sup className="cite" key={out.length}>
          {ctx.anchor && ctx.citationIds?.has(id) ? <a href={`#${ctx.anchor}-${id}`}>[{id}]</a> : `[${id}]`}
        </sup>,
      );
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
