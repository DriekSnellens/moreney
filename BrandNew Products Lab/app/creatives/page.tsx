import { DataTable, PageHeader, SourcePill } from "@/components/kit";
import { getLab } from "@/lib/lab";

export default async function CreativesPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader
        kicker="Creatives"
        title="Angles, hooks, and lines"
        description="Five hooks, three angles, and the copy that sits on an ad. Each row can be tied to a test and a campaign."
      />
      {state.creativeConcepts.length === 0 ? (
        <p className="text-sm text-muted">No creatives yet.</p>
      ) : (
        <div className="rounded-2xl border border-line bg-elev p-3">
          <DataTable
            columns={["Angle", "Hook", "Headline", "CTA", "Source"]}
            rows={state.creativeConcepts.map((creative) => [
              creative.angle,
              creative.hook,
              creative.headline,
              creative.cta,
              <SourcePill key={creative.id} provenance={creative.provenance} />,
            ])}
          />
        </div>
      )}
    </div>
  );
}
