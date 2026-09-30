import { DataTable, PageHeader, RecordLink, SourcePill } from "@/components/kit";
import { eur } from "@/lib/format";
import { getLab } from "@/lib/lab";

export default async function NichesPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader
        kicker="Niches"
        title="Micro-niches"
        description="A useful niche names a person and a situation. Pets and Home are categories, not niches."
      />
      {state.niches.length === 0 ? (
        <p className="text-sm text-muted">No niches in this workspace.</p>
      ) : (
        <div className="rounded-2xl border border-line bg-elev p-3">
          <DataTable
            columns={["Niche", "Problem", "Price range", "Source"]}
            rows={state.niches.map((niche) => [
              <RecordLink key={niche.id} href={`/niches/${niche.id}`}>{niche.name}</RecordLink>,
              niche.coreProblem,
              `${eur(niche.priceRangeMin)} – ${eur(niche.priceRangeMax)}`,
              <SourcePill key="src" provenance={niche.provenance} />,
            ])}
          />
        </div>
      )}
    </div>
  );
}
