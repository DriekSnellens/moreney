import { refreshAnalyst } from "@/app/actions";
import { PageHeader, SourcePill } from "@/components/kit";
import { Button } from "@/components/ui";
import { getLab } from "@/lib/lab";
import type { AnalystReport } from "@/types/domain";

export default async function AnalyticsPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader
        kicker="Analytics"
        title="What happened, and what might be next"
        description="The reading uses recorded metrics. It says possible explanation. It does not claim a cause, and it will not crown a winner on a tiny test."
      />
      <div className="mb-6 flex flex-wrap gap-2">
        {state.productTests.map((test) => {
          const product = state.products.find((item) => item.id === test.productId);
          return (
            <form key={test.id} action={refreshAnalyst}>
              <input type="hidden" name="testId" value={test.id} />
              <Button type="submit" variant="quiet">Refresh {product?.name}</Button>
            </form>
          );
        })}
      </div>
      <div className="grid gap-4">
        {state.aiRuns.map((run) => {
          const output = run.output as AnalystReport;
          return (
            <article key={run.id} className="rounded-2xl border border-line bg-elev p-5">
              <div className="flex items-center gap-2">
                <SourcePill provenance={run.provenance} />
                <span className="text-xs text-muted">{run.provider === "rules" ? "Rules engine" : run.provider}</span>
              </div>
              <h2 className="mt-3 font-serif text-2xl">What happened</h2>
              <ul className="mt-2 space-y-2 text-sm leading-6">
                {(output.whatHappened ?? []).map((line) => <li key={line}>{line}</li>)}
              </ul>
              <h3 className="mt-4 text-sm font-medium">Why it may have happened</h3>
              <ul className="mt-2 space-y-2 text-sm leading-6 text-muted">
                {(output.possibleExplanations ?? []).map((line) => <li key={line}>{line}</li>)}
              </ul>
              <h3 className="mt-4 text-sm font-medium">What to test next</h3>
              <ul className="mt-2 space-y-2 text-sm leading-6">
                {(output.whatToTestNext ?? []).map((line) => <li key={line}>{line}</li>)}
              </ul>
              <p className="mt-3 text-xs text-muted">{output.uncertainty}</p>
            </article>
          );
        })}
        {state.aiRuns.length === 0 ? <p className="text-sm text-muted">No readings yet.</p> : null}
      </div>
    </div>
  );
}
