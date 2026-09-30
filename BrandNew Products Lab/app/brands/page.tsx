import { DataTable, PageHeader, RecordLink, SourcePill } from "@/components/kit";
import { getLab } from "@/lib/lab";

export default async function BrandsPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader kicker="Brands" title="Draft brand systems" description="Names are working titles. Nothing here invents customers, reviews, or scarcity." />
      {state.brands.length === 0 ? <p className="text-sm text-muted">No brand drafts yet. Generate one from an opportunity.</p> : (
        <div className="rounded-2xl border border-line bg-elev p-3">
          <DataTable
            columns={["Brand", "Tagline", "Source"]}
            rows={state.brands.map((brand) => [
              <RecordLink key={brand.id} href={`/brands/${brand.id}`}>{brand.name}</RecordLink>,
              brand.tagline,
              <SourcePill key={`${brand.id}-src`} provenance={brand.provenance} />,
            ])}
          />
        </div>
      )}
    </div>
  );
}
