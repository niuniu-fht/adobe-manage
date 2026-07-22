import { describe, expect, it } from "vitest";
import { parseCookieText, parseCookieValue } from "./cookies";

describe("Cookie import parsing", () => {
  it("accepts a Cookie header", () => {
    expect(parseCookieText("Cookie: a=1; b=2")).toEqual([{ cookie: "a=1; b=2" }]);
  });

  it("converts a browser cookie array into one header", () => {
    expect(parseCookieValue([
      { name: "a", value: "1", domain: ".example.com" },
      { name: "b", value: "2" }
    ])).toEqual([{ cookie: "a=1; b=2" }]);
  });

  it("accepts named batches and items wrappers", () => {
    expect(parseCookieText(JSON.stringify({ items: [
      { name: "Account A", cookie: "a=1" },
      { email: "b@example.com", cookies: [{ name: "b", value: "2" }] }
    ] }))).toEqual([
      { name: "Account A", cookie: "a=1" },
      { name: "b@example.com", cookie: "b=2" }
    ]);
  });

  it("reports an invalid batch item", () => {
    expect(() => parseCookieText('[{"name":"Missing"}]')).toThrow("缺少 Cookie");
  });
});
