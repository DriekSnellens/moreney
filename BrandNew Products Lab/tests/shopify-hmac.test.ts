import { describe, expect, it } from "vitest";
import { shopifyHmac, verifyShopifyHmac } from "@/lib/shopify";

describe("shopify webhook signatures", () => {
  it("accepts a matching signature and rejects a tampered body", () => {
    const body = JSON.stringify({ id: 10, total_price: "29.95" });
    const secret = "test-secret";
    const signature = shopifyHmac(body, secret);
    expect(verifyShopifyHmac(body, signature, secret)).toBe(true);
    expect(verifyShopifyHmac(`${body} `, signature, secret)).toBe(false);
  });
});
