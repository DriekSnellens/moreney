import { DataTable, PageHeader, RecordLink, StatusPill } from "@/components/kit";
import { getLab } from "@/lib/lab";

export default async function ProductsPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader kicker="Products" title="Candidates" description="Open a product to see the supplier, the compliance gate, and the opportunity." />
      {state.products.length === 0 ? (
        <p className="text-sm text-muted">No products.</p>
      ) : (
        <div className="rounded-2xl border border-line bg-elev p-3">
          <DataTable
            columns={["Product", "Compliance", "Opportunity"]}
            rows={state.products.map((product) => {
              const opportunity = state.opportunities.find((item) => item.productId === product.id);
              return [
                <RecordLink key={product.id} href={`/products/${product.id}`}>{product.name}</RecordLink>,
                <StatusPill key="c" status={product.complianceStatus} />,
                opportunity ? <RecordLink href={`/opportunities/${opportunity.id}`}>Open</RecordLink> : "—",
              ];
            })}
          />
        </div>
      )}
    </div>
  );
}
