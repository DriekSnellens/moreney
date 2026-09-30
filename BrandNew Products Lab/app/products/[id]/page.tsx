import { notFound } from "next/navigation";
import { StoreDraftForm, VideoForm } from "@/components/forms";
import { PageHeader, RecordLink, Section, StatusPill } from "@/components/kit";
import { complianceDetail } from "@/services/compliance";
import { eur } from "@/lib/format";
import { getLab } from "@/lib/lab";

export default async function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const state = await getLab();
  const product = state.products.find((item) => item.id === id);
  if (!product) notFound();
  const niche = state.niches.find((item) => item.id === product.nicheId);
  const opportunity = state.opportunities.find((item) => item.productId === product.id);
  const supplierProduct = state.supplierProducts.find((item) => item.productId === product.id);
  const supplier = state.suppliers.find((item) => item.id === supplierProduct?.supplierId);
  const variants = state.variants.filter((item) => item.productId === product.id);
  return (
    <div>
      <PageHeader
        kicker="Product"
        title={product.name}
        description={product.description}
        actions={<StatusPill status={product.complianceStatus} />}
      />
      <p className="mb-6 max-w-3xl text-sm leading-6 text-muted">{complianceDetail(product.complianceStatus)}</p>
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Supplier record">
          {supplier ? (
            <dl className="grid gap-2 text-sm leading-6">
              <div>{supplier.name} · {supplier.verificationStatus}</div>
              <div>Country {supplier.country} · warehouse {supplier.warehouseCountry}</div>
              <div>Cost {supplierProduct ? eur(supplierProduct.productCost) : "—"} · shipping {supplierProduct ? eur(supplierProduct.shippingCost) : "—"}</div>
              <div>Returns: {supplier.returnLocation}</div>
              <div>VAT: {supplier.vatInformation}</div>
              <p className="text-muted">{supplier.notes}</p>
            </dl>
          ) : (
            <p className="text-sm text-muted">No supplier is linked.</p>
          )}
          {opportunity ? <p className="mt-4"><RecordLink href={`/opportunities/${opportunity.id}`}>Opportunity report</RecordLink></p> : null}
        </Section>
        <Section title="Variants">
          {variants.length === 0 ? <p className="text-sm text-muted">No variants.</p> : variants.map((variant) => (
            <p key={variant.id} className="text-sm">{variant.sku} · {variant.name}</p>
          ))}
          <p className="mt-3 text-sm text-muted">Niche: {niche?.name ?? "—"}</p>
        </Section>
        <Section title="Shopify draft">
          <StoreDraftForm productId={product.id} />
        </Section>
        <Section title="AI promo video">
          <VideoForm productId={product.id} />
        </Section>
      </div>
    </div>
  );
}
