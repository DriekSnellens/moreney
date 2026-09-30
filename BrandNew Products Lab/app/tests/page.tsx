import { DataTable, PageHeader, RecordLink, StatusPill } from "@/components/kit";
import { eur } from "@/lib/format";
import { getLab } from "@/lib/lab";
import { calculateProfit } from "@/services/profit";

export default async function TestsPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader
        kicker="Tests"
        title="Controlled bets"
        description="Research is not a result. Profitable stays locked until actual net profit and enough orders exist."
      />
      {state.productTests.length === 0 ? (
        <p className="text-sm text-muted">No tests. Start one from an opportunity.</p>
      ) : (
        <div className="rounded-2xl border border-line bg-elev p-3">
          <DataTable
            columns={["Product", "Status", "Budget", "Net"]}
            rows={state.productTests.map((test) => {
              const product = state.products.find((item) => item.id === test.productId);
              const profit = calculateProfit(test.assumptions, test.actuals);
              return [
                <RecordLink key={test.id} href={`/tests/${test.id}`}>{product?.name}</RecordLink>,
                <StatusPill key={`${test.id}-status`} status={test.status} />,
                eur(test.testBudget),
                <span key="net" className={profit.netProfit < 0 ? "text-bad" : ""}>{eur(profit.netProfit)} · {profit.mode}</span>,
              ];
            })}
          />
        </div>
      )}
    </div>
  );
}
