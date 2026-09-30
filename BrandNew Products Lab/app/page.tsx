import Link from "next/link";
import { DataTable, Metric, PageHeader, RecordLink, SourcePill, StatusPill } from "@/components/kit";
import { eur } from "@/lib/format";
import { getLabEnv } from "@/lib/env";
import { getLab } from "@/lib/lab";
import { buildReport } from "@/services/report";
import type { AnalystReport } from "@/types/domain";

export default async function DashboardPage() {
  const state = await getLab();
  const env = getLabEnv();
  const today = new Date().toISOString().slice(0, 10);
  const todayMetrics = state.dailyMetrics.filter((metric) => metric.day === today);
  const spend = todayMetrics.reduce((sum, metric) => sum + metric.spend, 0);
  const revenue = todayMetrics.reduce((sum, metric) => sum + metric.revenue, 0);
  const net = todayMetrics.reduce((sum, metric) => sum + metric.netProfit, 0);
  const discovered = state.opportunities.filter((item) => item.createdAt.slice(0, 10) === today);
  const active = state.productTests.filter((test) =>
    ["VALIDATION", "READY_TO_TEST", "TESTING", "PROMISING"].includes(test.status),
  );
  const promising = state.productTests.filter((test) => test.status === "PROMISING");
  const ranked = [...state.opportunities].sort((a, b) => {
    const left = buildReport(state, a).score;
    const right = buildReport(state, b).score;
    return right - left;
  });

  return (
    <div>
      <PageHeader
        kicker="Today"
        title="What deserves a test"
        description="A product is interesting only after costs, ads, refunds, and the execution buffer. Trend heat is not a result."
      />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Opportunities discovered" value={String(discovered.length)} hint="Created today in this workspace." />
        <Metric label="Products being tested" value={String(active.length)} hint="Validation through promising." />
        <Metric label="Ad spend" value={eur(spend)} hint={env.dataMode === "demo" ? "Demo spend for today." : "Recorded spend for today."} />
        <Metric label="Revenue" value={eur(revenue)} hint="Gross recorded revenue, not profit." />
        <Metric
          label="Net profit"
          value={eur(net)}
          tone={net < 0 ? "bad" : net > 0 ? "good" : "default"}
          hint="From today's metric rows. A slice, not a company P&L."
        />
        <Metric label="Active tests" value={String(active.length)} />
        <Metric label="Promising products" value={String(promising.length)} hint="None until a test earns the status." />
        <Metric label="Workspace" value={env.dataMode === "demo" ? "Demo" : "Live"} hint="Switch with DATA_MODE." />
      </div>

      <div className="mt-8 grid gap-6 xl:grid-cols-2">
        <section className="rounded-2xl border border-line bg-elev p-5">
          <h2 className="font-serif text-2xl">Top opportunities</h2>
          <div className="mt-4">
            {ranked.length === 0 ? (
              <p className="text-sm text-muted">No opportunities yet. Import one from Discover, or connect a feed.</p>
            ) : (
              <DataTable
                columns={["Product", "Evidence", "Score", "Source"]}
                rows={ranked.slice(0, 5).map((opportunity) => {
                  const report = buildReport(state, opportunity);
                  return [
                    <RecordLink key={opportunity.id} href={`/opportunities/${opportunity.id}`}>
                      {report.product?.name ?? "Untitled"}
                    </RecordLink>,
                    report.evidenceLabel,
                    <span key="score" className="num">
                      {report.score.toFixed(0)}
                    </span>,
                    <SourcePill key="src" provenance={opportunity.provenance} />,
                  ];
                })}
              />
            )}
          </div>
        </section>
        <section className="rounded-2xl border border-line bg-elev p-5">
          <h2 className="font-serif text-2xl">Active tests</h2>
          <div className="mt-4">
            {active.length === 0 ? (
              <p className="text-sm text-muted">No test is running. Open one from an opportunity when the economics are worth a small budget.</p>
            ) : (
              <DataTable
                columns={["Test", "Status", "Budget"]}
                rows={active.map((test) => {
                  const product = state.products.find((item) => item.id === test.productId);
                  return [
                    <RecordLink key={test.id} href={`/tests/${test.id}`}>
                      {product?.name ?? "Test"}
                    </RecordLink>,
                    <StatusPill key="status" status={test.status} />,
                    eur(test.testBudget),
                  ];
                })}
              />
            )}
          </div>
        </section>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <section className="rounded-2xl border border-line bg-elev p-5">
          <h2 className="font-serif text-2xl">Recent readings</h2>
          <ul className="mt-4 space-y-4">
            {state.aiRuns.slice(0, 3).map((run) => {
              const output = run.output as AnalystReport;
              return (
                <li key={run.id} className="border-t border-line pt-4 first:border-0 first:pt-0">
                  <div className="mb-2 flex items-center gap-2">
                    <SourcePill provenance={run.provenance} />
                    <span className="text-xs text-muted">{run.provider === "rules" ? "Rules reading" : run.provider}</span>
                  </div>
                  <p className="text-sm leading-6">{output.whatHappened?.[0] ?? run.inputSummary}</p>
                  <p className="mt-1 text-xs text-muted">{output.uncertainty}</p>
                </li>
              );
            })}
            {state.aiRuns.length === 0 ? <li className="text-sm text-muted">No reading yet.</li> : null}
          </ul>
          <Link href="/analytics" className="mt-4 inline-block text-sm underline decoration-line underline-offset-4">
            Open analytics
          </Link>
        </section>
        <section className="rounded-2xl border border-line bg-elev p-5">
          <h2 className="font-serif text-2xl">Recent video jobs</h2>
          <ul className="mt-4 space-y-3">
            {state.videoJobs.slice(0, 4).map((job) => {
              const product = state.products.find((item) => item.id === job.productId);
              return (
                <li key={job.id} className="flex items-center justify-between gap-3 border-t border-line pt-3 first:border-0 first:pt-0">
                  <div>
                    <p className="text-sm">{product?.name}</p>
                    <p className="text-xs text-muted">{job.note}</p>
                  </div>
                  <StatusPill status={job.status} />
                </li>
              );
            })}
            {state.videoJobs.length === 0 ? <li className="text-sm text-muted">No video jobs.</li> : null}
          </ul>
        </section>
      </div>
    </div>
  );
}
