import { DataTable, PageHeader, RecordLink, SourcePill } from "@/components/kit";
import { eur } from "@/lib/format";
import { getLab } from "@/lib/lab";
import { buildReport } from "@/services/report";

export default async function OpportunitiesPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader
        kicker="Opportunities"
        title="Hypotheses with a cost stack"
        description="The score is a weighted reading with a sentence on every factor. It does not promote a product to profitable."
      />
      {state.opportunities.length === 0 ? (
        <p className="text-sm text-muted">No opportunities. Import one, or switch to demo mode to see the worked example.</p>
      ) : (
        <div className="rounded-2xl border border-line bg-elev p-3">
          <DataTable
            columns={["Product", "Niche", "Evidence", "Score", "Est. net", "Source"]}
            rows={state.opportunities.map((opportunity) => {
              const report = buildReport(state, opportunity);
              return [
                <RecordLink key={opportunity.id} href={`/opportunities/${opportunity.id}`}>
                  {report.product?.name}
                </RecordLink>,
                report.niche?.name ?? "—",
                report.evidenceLabel,
                <span key="score" className="num">{report.score.toFixed(0)}</span>,
                <span key="net" className={`num ${report.profit.netProfit < 0 ? "text-bad" : ""}`}>
                  {eur(report.profit.netProfit)}
                </span>,
                <SourcePill key="src" provenance={opportunity.provenance} />,
              ];
            })}
          />
        </div>
      )}
    </div>
  );
}
