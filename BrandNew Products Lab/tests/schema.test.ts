import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";

const REQUIRED = [
  "users",
  "niches",
  "products",
  "product_variants",
  "suppliers",
  "supplier_products",
  "trend_signals",
  "market_signals",
  "competition_signals",
  "opportunities",
  "brands",
  "stores",
  "store_products",
  "creative_concepts",
  "creative_assets",
  "video_jobs",
  "ad_accounts",
  "campaigns",
  "ad_sets",
  "ads",
  "ad_metrics",
  "orders",
  "order_items",
  "refunds",
  "profit_events",
  "ai_runs",
  "workflow_runs",
  "experiments",
  "experiment_variants",
  "product_tests",
  "daily_metrics",
];

describe("postgres schema", () => {
  it("applies the initial migration and accepts a product row", async () => {
    const sql = readFileSync(path.join(process.cwd(), "db/migrations/001_init.sql"), "utf8");
    const db = new PGlite();
    await db.exec(sql);
    const tables = await db.query<{ table_name: string }>(
      "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
    );
    const names = new Set(tables.rows.map((row) => row.table_name));
    for (const table of REQUIRED) {
      expect(names.has(table)).toBe(true);
    }

    const orgId = "00000000-0000-4000-8000-000000000001";
    const nicheId = "00000000-0000-4000-8000-000000000010";
    const productId = "00000000-0000-4000-8000-000000000020";
    await db.query("INSERT INTO organizations (id, name) VALUES ($1, $2)", [orgId, "Personal"]);
    await db.query(
      `INSERT INTO niches (id, organization_id, name, provenance)
       VALUES ($1, $2, $3, $4)`,
      [nicheId, orgId, "Owners of long-haired dogs who struggle with car hair", "demo"],
    );
    await db.query(
      `INSERT INTO products (
         id, organization_id, niche_id, name, compliance_status, provenance
       ) VALUES ($1, $2, $3, $4, $5, $6)`,
      [productId, orgId, nicheId, "Compact car dog-hair roller", "no_flags_found", "demo"],
    );
    const products = await db.query<{ name: string }>("SELECT name FROM products WHERE id = $1", [productId]);
    expect(products.rows[0]?.name).toBe("Compact car dog-hair roller");
    await db.close();
  });
});
