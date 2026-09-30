import { notFound } from "next/navigation";
import { generateCreativesAction, refreshAnalyst } from "@/app/actions";
import { CampaignForm, MoveTestForm } from "@/components/forms";
import { DataTable, PageHeader, Section, SourcePill, StatusPill } from "@/components/kit";
import { Button } from "@/components/ui";
import { eur } from "@/lib/format";
import { getLab } from "@/lib/lab";
import { analyzePerformance } from "@/services/analyst";
import { allowedTransitions } from "@/services/product-tests";
import { calculateProfit } from "@/services/profit";
import type { AnalystReport } from "@/types/domain";

export default async function TestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const state = await getLab();
  const test = state.productTests.find((item) => item.id === id);
  if (!test) notFound();
  const product = state.products.find((item) => item.id === test.productId);
  const profit = calculateProfit(test.assumptions, test.actuals);
  const creatives = state.creativeConcepts.filter((item) => item.productTestId === test.id);
  const campaigns = state.campaigns.filter((item) => item.productTestId === test.id);
  const ads = state.ads.filter((ad) => campaigns.some((campaign) => state.adSets.some((set) => set.id === ad.adSetId && set.campaignId === campaign.id)));
  const metrics = state.adMetrics.filter((metric) => ads.some((ad) => ad.id === metric.adId));
  const reading = analyzePerformance({
    metrics,
    profit,
  });
  const experiments = state.experiments.filter((item) => item.productTestId === test.id);
  return (
    <div>
      <PageHeader
        kicker="Product test"
        title={product?.name ?? "Test"}
        description={test.resultSummary}
        actions={<StatusPill status={test.status} />}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Status" eyebrow={`${test.startsOn} → ${test.endsOn}`}>
          <p className="mb-3 text-sm text-muted">Budget {eur(test.testBudget)}. Net on the current model {eur(profit.netProfit)} ({profit.mode}).</p>
          <MoveTestForm testId={test.id} options={allowedTransitions(test.status)} />
          <p className="mt-3 text-xs text-muted">KPIs: {test.kpis.join(", ")}</p>
        </Section>
        <Section title="Reading" eyebrow="Possible explanations">
          <Reading report={reading} />
          <form action={refreshAnalyst} className="mt-4">
            <input type="hidden" name="testId" value={test.id} />
            <Button type="submit" variant="quiet">Refresh reading</Button>
          </form>
        </Section>
        <Section title="Creatives">
          {creatives.length === 0 ? (
            <form action={generateCreativesAction}>
              <input type="hidden" name="testId" value={test.id} />
              <Button type="submit">Generate 5 hooks</Button>
            </form>
          ) : (
            <ul className="space-y-3 text-sm leading-6">
              {creatives.map((creative) => (
                <li key={creative.id}>
                  <span className="text-xs uppercase tracking-[0.14em] text-muted">{creative.angle}</span>
                  <p>{creative.hook}</p>
                  <p className="text-muted">{creative.primaryText}</p>
                  <SourcePill provenance={creative.provenance} />
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title="Ad draft">
          <CampaignForm testId={test.id} creatives={creatives.map((item) => ({ id: item.id, headline: item.headline }))} />
          <p className="mt-3 text-xs text-muted">Approval does not spend money. Campaigns stay paused inside the lab.</p>
        </Section>
      </div>
      {experiments.length > 0 ? (
        <section className="mt-6 rounded-2xl border border-line bg-elev p-5">
          <h2 className="font-serif text-2xl">Experiments</h2>
          {experiments.map((experiment) => {
            const variants = state.experimentVariants.filter((item) => item.experimentId === experiment.id);
            return (
              <div key={experiment.id} className="mt-4">
                <p className="text-sm">{experiment.hypothesis}</p>
                <p className="mt-1 text-xs text-muted">{experiment.uncertaintyNote}</p>
                <div className="mt-3">
                  <DataTable
                    columns={["Variant", "Impressions", "Clicks", "Purchases", "Spend"]}
                    rows={variants.map((variant) => [
                      variant.name,
                      String(variant.impressions),
                      String(variant.clicks),
                      String(variant.purchases),
                      eur(variant.spend),
                    ])}
                  />
                </div>
              </div>
            );
          })}
        </section>
      ) : null}
    </div>
  );
}

function Reading({ report }: { report: AnalystReport }) {
  return (
    <div className="grid gap-3 text-sm leading-6">
      {report.whatHappened.map((line) => <p key={line}>{line}</p>)}
      {report.possibleExplanations.map((line) => <p key={line} className="text-muted">{line}</p>)}
      {report.whatToTestNext.map((line) => <p key={line}>Next: {line}</p>)}
      <p className="text-xs text-muted">{report.uncertainty}</p>
    </div>
  );
}
