import { createHmac, timingSafeEqual } from "node:crypto";

export function shopifyHmac(rawBody: string, secret: string): string {
  return createHmac("sha256", secret).update(rawBody, "utf8").digest("base64");
}

export function verifyShopifyHmac(rawBody: string, header: string, secret: string): boolean {
  const digest = shopifyHmac(rawBody, secret);
  const left = Buffer.from(digest);
  const right = Buffer.from(header);
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}
