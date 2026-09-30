import { DataTable, Metric, PageHeader, RecordLink, SourcePill } from "@/components/kit";
import { eur, pct } from "@/lib/format";
import { getLab } from "@/lib/lab";
import { calculateProfit } from "@/services/profit";

export default async function ProfitPage() {
  const state = await getLab();
  const rows = state.productTests.map((test) => {
    const profit = calculateProfit(test.assumptions, test.actuals);
    const product = state.products.find((item) => item.id === test.productId);
    return { test, profit, product };
  });
  const estimates = state.opportunities.filter((item) => !state.productTests.some((test) => test.opportunityId === item.id));
  const net = rows.reduce((sum, row) => sum + (row.test.actuals ? row.profit.netProfit : 0), 0);
  return (
    <div>
      <PageHeader
        kicker="Profit"
        title="Net, after the stack"
        description="Gross margin is the start of the sentence. Net profit is the end. Actuals replace the lines they cover and leave the rest labeled as estimates."
      />
      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <Metric label="Actual net on tests" value={eur(net)} tone={net < 0 ? "bad" : "good"} hint="Sum of tests that have actuals." />
        <Metric label="Tests" value={String(rows.length)} />
        <Metric label="Untested hypotheses" value={String(estimates.length)} />
      </div>
      {rows.length === 0 ? <p className="text-sm text-muted">No tests to cost.</p> : (
        <div className="rounded-2xl border border-line bg-elev p-3">
          <DataTable
            columns={["Product", "Mode", "Contribution", "After ads", "Net", "Margin"]}
            rows={rows.map(({ test, profit, product }) => [
              <RecordLink key={test.id} href={`/tests/${test.id}`}>{product?.name}</RecordLink>,
              profit.mode,
              eur(profit.contributionMargin),
              eur(profit.contributionAfterAds),
              <span key="net" className={profit.netProfit < 0 ? "text-bad" : "text-good"}>{eur(profit.netProfit)}</span>,
              pct(profit.netMargin),
            ])}
          />
        </div>
      )}
      <section className="mt-6 rounded-2xl border border-line bg-elev p-5">
        <h2 className="font-serif text-2xl">Profit events</h2>
        <ul className="mt-4 space-y-3 text-sm">
          {state.profitEvents.map((event) => (
            <li key={event.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3">
              <span>{event.note}</span>
              <span className="flex items-center gap-2">
                <span className={event.netProfit < 0 ? "text-bad" : ""}>{eur(event.netProfit)}</span>
                <SourcePill provenance={event.provenance} />
              </span>
            </li>
          ))}
          {state.profitEvents.length === 0 ? <li className="text-muted">No profit events.</li> : null}
        </ul>
      </section>
    </div>
  );
}
