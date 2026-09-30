import { DataTable, PageHeader, SourcePill, StatusPill } from "@/components/kit";
import { getLabEnv } from "@/lib/env";
import { getLab } from "@/lib/lab";

export default async function StoresPage() {
  const state = await getLab();
  const env = getLabEnv();
  const shopify = Boolean(env.shopifyStoreUrl && env.shopifyAccessToken);
  return (
    <div>
      <PageHeader
        kicker="Stores"
        title="Shopify drafts"
        description="Publishing is a separate decision. This page only holds drafts you approved, and live mode sends them to Shopify as DRAFT."
      />
      <p className="mb-6 text-sm text-muted">
        {shopify ? "Shopify credentials are set." : "Provider not configured. Demo drafts stay inside the lab."}
      </p>
      <div className="rounded-2xl border border-line bg-elev p-3">
        {state.storeProducts.length === 0 ? (
          <p className="p-3 text-sm text-muted">No drafts. Approve one from a product page.</p>
        ) : (
          <DataTable
            columns={["Product", "Status", "SEO", "Source"]}
            rows={state.storeProducts.map((item) => {
              const product = state.products.find((row) => row.id === item.productId);
              return [
                product?.name ?? "Product",
                <StatusPill key="s" status={item.status} />,
                item.seoTitle,
                <SourcePill key="p" provenance={item.provenance} />,
              ];
            })}
          />
        )}
      </div>
      {state.storeProducts[0] ? (
        <section className="mt-6 rounded-2xl border border-line bg-elev p-5">
          <h2 className="font-serif text-2xl">Latest draft body</h2>
          <div className="prose mt-4 max-w-3xl text-sm leading-6" dangerouslySetInnerHTML={{ __html: state.storeProducts[0].bodyHtml }} />
        </section>
      ) : null}
    </div>
  );
}
