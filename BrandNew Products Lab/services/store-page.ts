import type { Brand, Niche, Product, Supplier, SupplierProduct } from "@/types/domain";

export function draftStorePage(input: {
  product: Product;
  brand: Brand | null;
  niche: Niche | null;
  supplier: Supplier | null;
  supplierProduct: SupplierProduct | null;
}) {
  const name = input.brand?.name ?? input.product.name;
  const shipping =
    input.supplier && input.supplier.shippingDaysMin !== null && input.supplier.shippingDaysMax !== null
      ? `${input.supplier.shippingDaysMin}–${input.supplier.shippingDaysMax} days from ${input.supplier.warehouseCountry || "an unconfirmed warehouse"}`
      : "Shipping time is not confirmed.";
  const seoTitle = `${input.product.name} · ${name}`;
  const seoDescription = (input.brand?.positioning ?? input.product.description).slice(0, 155);
  const body = [
    `<p>${escapeHtml(input.niche?.coreProblem ?? input.product.description)}</p>`,
    `<h2>What it is</h2>`,
    `<p>${escapeHtml(input.product.description)}</p>`,
    `<h2>Who it is for</h2>`,
    `<p>${escapeHtml(input.niche?.targetAudience ?? "Not specified.")}</p>`,
    `<h2>Shipping and returns</h2>`,
    `<p>${escapeHtml(shipping)}. Return location: ${escapeHtml(input.supplier?.returnLocation || "not confirmed")}.</p>`,
    `<p>${escapeHtml(input.supplier?.vatInformation || "VAT treatment is not confirmed.")}</p>`,
    `<h2>Documents</h2>`,
    `<p>${escapeHtml(input.product.productDocuments || "None stored.")}</p>`,
    `<p>${escapeHtml(input.product.safetyInformation || "No safety information is stored.")}</p>`,
  ];
  if (input.supplierProduct && !input.supplierProduct.euWarehouse) {
    body.push("<p>The supplier record does not show an EU warehouse.</p>");
  }
  return { seoTitle, seoDescription, bodyHtml: body.join("\n") };
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
