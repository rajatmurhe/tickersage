import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Markdown from "./Markdown";

/** Render to HTML. Exact-string assertions pin both the elements created and the text escaping. */
function html(text: string, citationIds?: number[], anchor = "a1") {
  return renderToStaticMarkup(
    <Markdown text={text} citationIds={citationIds && new Set(citationIds)} anchor={anchor} />,
  );
}

/** Every opening tag name in the markup, in order. */
const tags = (markup: string) => Array.from(markup.matchAll(/<([a-z][a-z0-9]*)/gi), (m) => m[1].toLowerCase());

describe("Markdown", () => {
  it("renders paragraphs, joining wrapped lines", () => {
    expect(html("First line\nsame paragraph.\n\nSecond paragraph.")).toBe(
      "<p>First line same paragraph.</p><p>Second paragraph.</p>",
    );
  });

  it("renders **bold**", () => {
    expect(html("BTC is **up 3.2%** today.")).toBe("<p>BTC is <strong>up 3.2%</strong> today.</p>");
  });

  it("renders _italics_ but leaves snake_case identifiers alone", () => {
    expect(html("Prices are _approximate_, and _delayed_.")).toBe(
      "<p>Prices are <em>approximate</em>, and <em>delayed</em>.</p>",
    );
    expect(html("Sorted by market_cap_rank and price_change_24h.")).toBe(
      "<p>Sorted by market_cap_rank and price_change_24h.</p>",
    );
  });

  it('renders the "_Data as of ..._" footer as one italic paragraph', () => {
    // Format from backend/app/guardrails.py.
    const footer = "_Data as of 21:00 UTC on 2026-10-18 from CoinGecko. Market data only, not investment advice._";
    expect(html(`BTC is $67,000 [1].\n\n${footer}`, [1])).toBe(
      '<p>BTC is $67,000 <sup class="cite"><a href="#a1-1">[1]</a></sup>.</p>' +
        "<p><em>Data as of 21:00 UTC on 2026-10-18 from CoinGecko. Market data only, not investment advice.</em></p>",
    );
  });

  it("renders pipe tables with inline formatting in cells", () => {
    const table = [
      "| Asset | Price | 24h |",
      "|---|---:|:---:|",
      "| **BTC** | $67,000 [1] | +3.2% |",
      "| ETH | $3,100 | _n/a_ |",
    ];
    expect(html(table.join("\n"), [1])).toBe(
      '<div class="table-wrap"><table>' +
        "<thead><tr><th>Asset</th><th>Price</th><th>24h</th></tr></thead>" +
        "<tbody>" +
        '<tr><td><strong>BTC</strong></td><td>$67,000 <sup class="cite"><a href="#a1-1">[1]</a></sup></td><td>+3.2%</td></tr>' +
        "<tr><td>ETH</td><td>$3,100</td><td><em>n/a</em></td></tr>" +
        "</tbody></table></div>",
    );
  });

  it("does not treat pipes without a rule line as a table", () => {
    expect(html("| not | a table |\n| just | text |")).toBe("<p>| not | a table | | just | text |</p>");
  });

  it('renders "-" lists, an intro line, and wrapped continuation lines', () => {
    const text = [
      "Drivers:",
      "- ETF inflows of **$1.2B** [2]",
      "  over the week.",
      "* Lower funding rates",
      "- Short liquidations",
    ];
    expect(html(text.join("\n"), [2])).toBe(
      "<div><p>Drivers:</p><ul>" +
        '<li>ETF inflows of <strong>$1.2B</strong> <sup class="cite"><a href="#a1-2">[2]</a></sup> over the week.</li>' +
        "<li>Lower funding rates</li>" +
        "<li>Short liquidations</li>" +
        "</ul></div>",
    );
  });

  it("links [n] citation markers only when the id is in the source list", () => {
    expect(html("Up [1], down [2], flat [3].", [1, 3])).toBe(
      '<p>Up <sup class="cite"><a href="#a1-1">[1]</a></sup>, ' +
        'down <sup class="cite">[2]</sup>, ' +
        'flat <sup class="cite"><a href="#a1-3">[3]</a></sup>.</p>',
    );
  });

  it("does not link citation markers without an anchor or a source list", () => {
    const noAnchor = renderToStaticMarkup(<Markdown text="See [1]." citationIds={new Set([1])} />);
    expect(noAnchor).toBe('<p>See <sup class="cite">[1]</sup>.</p>');
    expect(html("See [1].")).toBe('<p>See <sup class="cite">[1]</sup>.</p>');
  });

  describe("untrusted text (news headlines)", () => {
    it("renders HTML tags as text without creating elements", () => {
      const markup = html("Headline: <img src=x onerror=alert(1)> BTC pumps");
      expect(markup).toBe("<p>Headline: &lt;img src=x onerror=alert(1)&gt; BTC pumps</p>");
      expect(tags(markup)).toEqual(["p"]);
    });

    it("escapes a <script> headline inside bold, a list item and a table cell", () => {
      const headline = '**<script>alert("x")</script>**';
      const escaped = "<strong>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;</strong>";
      expect(html(headline)).toBe(`<p>${escaped}</p>`);
      expect(html(`- ${headline} [1]`, [1])).toBe(
        `<div><ul><li>${escaped} <sup class="cite"><a href="#a1-1">[1]</a></sup></li></ul></div>`,
      );
      expect(tags(html(`| News |\n|---|\n| ${headline} |`))).toEqual(
        ["div", "table", "thead", "tr", "th", "tbody", "tr", "td", "strong"],
      );
    });

    it("escapes attribute-breaking quotes and event handlers", () => {
      const markup = html(`"><svg onload=alert(1)> & 'quoted' <a href="javascript:alert(1)">click</a>`);
      expect(tags(markup)).toEqual(["p"]);
      expect(markup).not.toContain("<svg");
      expect(markup).not.toContain("<a ");
      expect(markup).toContain("&lt;svg onload=alert(1)&gt; &amp; &#x27;quoted&#x27;");
    });

    it("does not turn markdown link syntax into links", () => {
      const markup = html("[click me](javascript:alert(1)) and [1](javascript:alert(1))", [1]);
      expect(markup).toBe(
        '<p>[click me](javascript:alert(1)) and <sup class="cite"><a href="#a1-1">[1]</a></sup>(javascript:alert(1))</p>',
      );
    });
  });
});
