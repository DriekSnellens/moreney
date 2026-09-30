import { DataTable, PageHeader, SourcePill } from "@/components/kit";
import { eur } from "@/lib/format";
import { getLab } from "@/lib/lab";

export default async function OrdersPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader
        kicker="Orders"
        title="Sales with attribution"
        description="An order keeps the campaign, creative, product, and experiment ids that were on the landing URL."
      />
      {state.orders.length === 0 ? (
        <p className="text-sm text-muted">No orders. A signed Shopify orders/create webhook can record one in live mode.</p>
      ) : (
        <div className="rounded-2xl border border-line bg-elev p-3">
          <DataTable
            columns={["When", "Total", "Campaign", "Creative", "Product", "Source"]}
            rows={state.orders.map((order) => {
              const product = state.products.find((item) => item.id === order.productId);
              return [
                order.orderedAt.slice(0, 16).replace("T", " "),
                eur(order.grossTotal),
                order.campaignId ?? "—",
                order.creativeId ?? "—",
                product?.name ?? order.productId ?? "—",
                <SourcePill key={order.id} provenance={order.provenance} />,
              ];
            })}
          />
        </div>
      )}
    </div>
  );
}
