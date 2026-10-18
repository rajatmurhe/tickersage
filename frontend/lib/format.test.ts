import { describe, expect, it } from "vitest";
import { safeUrl } from "./format";

describe("safeUrl", () => {
  it("returns absolute http(s) URLs in normalized form", () => {
    expect(safeUrl("https://www.coindesk.com/markets/2026/10/18/btc?ref=rss")).toBe(
      "https://www.coindesk.com/markets/2026/10/18/btc?ref=rss",
    );
    expect(safeUrl("http://example.com")).toBe("http://example.com/");
    expect(safeUrl("HTTPS://Example.COM/a b")).toBe("https://example.com/a%20b");
  });

  it.each([
    "javascript:alert(1)",
    "JavaScript:alert(document.cookie)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "ftp://example.com/x",
  ])("rejects the non-http scheme in %j", (url) => {
    expect(safeUrl(url)).toBeNull();
  });

  it.each(["/news/1", "news/1", "./x", "../x", "//evil.example/x", "#top", "?q=1", "example.com/path"])(
    "rejects the relative URL %j",
    (url) => {
      expect(safeUrl(url)).toBeNull();
    },
  );

  it("rejects empty and missing values", () => {
    expect(safeUrl("")).toBeNull();
    expect(safeUrl(null)).toBeNull();
    expect(safeUrl(undefined)).toBeNull();
    expect(safeUrl("not a url")).toBeNull();
  });
});
