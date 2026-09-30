import Link from "next/link";
import { notFound } from "next/navigation";
import { CreateTestForm, ProfitEditor, VideoForm } from "@/components/forms";
import { generateBrandAction, generateCreativesAction } from "@/app/actions";
import { PageHeader, Section, SourcePill, Sparkline, StatusPill } from "@/components/kit";
import { Button } from "@/components/ui";
import { complianceDetail, complianceLabel } from "@/services/compliance";
import { eur, pct } from "@/lib/format";
import { getLab } from "@/lib/lab";
import { buildReport } from "@/services/report";

export default async function OpportunityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const state = await getLab();
  const opportunity = state.opportunities.find((item) => item.id === id);
  if (!opportunity) notFound();
  const report = buildReport(state, opportunity);
  if (!report.product || !report.niche) notFound();
  const netTone = report.profit.netProfit < 0 ? "text-bad" : "text-good";

  return (
    <div>
      <PageHeader
        kicker={report.evidenceLabel}
        title={report.product.name}
        description={report.niche.name}
        actions={
          <div className="flex flex-wrap gap-2">
            <SourcePill provenance={opportunity.provenance} />
            <StatusPill status={report.product.complianceStatus} />
          </div>
        }
      />
      <p className="mb-6 max-w-3xl text-sm leading-6 text-muted">{complianceDetail(report.product.complianceStatus)} {complianceLabel(report.product.complianceStatus)}.</p>

      <div className="grid gap-4 md:grid-cols-4">
        <Fact label="Supplier" value={report.supplier?.name ?? "None"} note={report.supplier?.verificationStatus ?? "No record"} />
        <Fact label="Warehouse" value={report.supplier?.warehouseCountry || "Unknown"} note={report.supplierProduct?.euWarehouse ? "EU warehouse on the record" : "No EU warehouse flag"} />
        <Fact label="Landed pieces" value={eur(opportunity.assumptions.productCost + opportunity.assumptions.shippingCost)} note="Product cost plus outbound shipping. Estimate unless actuals exist." />
        <Fact label="Expected retail" value={eur(opportunity.assumptions.retailPriceIncVat)} note="VAT-inclusive assumption." />
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_0.8fr]">
        <div className="grid gap-6">
          <Section title="Opportunity overview" eyebrow="Explained factors">
            <div className="mb-4 flex items-end justify-between gap-4">
              <p className="text-sm text-muted">Weighted score {report.score.toFixed(0)}. Each line has a reason. The score is not a verdict.</p>
              <Sparkline values={report.trends[0]?.series ?? []} />
            </div>
            <ul className="divide-y divide-line">
              {report.factors.map((factor) => (
                <li key={factor.key} className="grid gap-2 py-3 md:grid-cols-[160px_64px_1fr] md:items-start">
                  <span className="text-sm">{factor.label}</span>
                  <span className="num text-sm text-muted">{factor.score.toFixed(0)}</span>
                  <span className="text-sm leading-6 text-muted">{factor.rationale}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-muted">{report.trend.note}</p>
          </Section>

          <Section title="Profit model" eyebrow={report.profit.mode}>
            <p className={`num font-serif text-4xl ${netTone}`}>{eur(report.profit.netProfit)}</p>
            <p className="mt-1 text-sm text-muted">
              Contribution {eur(report.profit.contributionMargin)} · after ads {eur(report.profit.contributionAfterAds)} · gross margin {pct(report.profit.grossMargin)}
            </p>
            <div className="mt-5">
              <ProfitEditor opportunityId={opportunity.id} assumptions={opportunity.assumptions} actuals={opportunity.actuals} />
            </div>
          </Section>

          <Section title="Competition" eyebrow="Separate from the score">
            {report.competition.length === 0 ? (
              <p className="text-sm text-muted">No competition note is stored.</p>
            ) : (
              report.competition.map((signal) => (
                <p key={signal.id} className="text-sm leading-6">
                  {signal.note} {signal.isEstimate ? "This is an estimate." : "This is recorded data."}
                </p>
              ))
            )}
            <p className="mt-3 text-sm text-muted">Main risk if you advertise the category instead of the situation: you buy the expensive generic click.</p>
          </Section>

          <Section title="Target audience" eyebrow="Niche">
            <dl className="grid gap-3 text-sm leading-6">
              <div><dt className="text-muted">Audience</dt><dd>{report.niche.targetAudience}</dd></div>
              <div><dt className="text-muted">Problem</dt><dd>{report.niche.coreProblem}</dd></div>
              <div><dt className="text-muted">Motivation</dt><dd>{report.niche.buyingMotivation}</dd></div>
              <div><dt className="text-muted">Why this niche</dt><dd>{report.niche.whyInteresting}</dd></div>
              <div><dt className="text-muted">Season and place</dt><dd>{report.niche.seasonality}. {report.niche.geographicRelevance}.</dd></div>
            </dl>
          </Section>

          <Section title="Positioning" eyebrow="Draft, not a fact">
            <p className="text-sm leading-6">{opportunity.positioning}</p>
            <div className="mt-3"><SourcePill kind={opportunity.positioningKind} /></div>
          </Section>

          <Section title="Brand" eyebrow="A niche brand, not a template shout">
            {report.brand ? (
              <div className="grid gap-2 text-sm leading-6">
                <p className="font-serif text-2xl">{report.brand.name}</p>
                <p className="text-muted">{report.brand.nameNote}</p>
                <p>{report.brand.tagline}</p>
                <p>{report.brand.positioning}</p>
                <p>Color: {report.brand.colorDirection}</p>
                <p>Type: {report.brand.typographyDirection}</p>
                <Link href={`/brands/${report.brand.id}`} className="underline decoration-line underline-offset-4">Open the brand draft</Link>
              </div>
            ) : (
              <form action={generateBrandAction}>
                <input type="hidden" name="opportunityId" value={opportunity.id} />
                <Button type="submit" variant="quiet">Draft a brand</Button>
              </form>
            )}
          </Section>

          <Section title="AI video" eyebrow="Concept before any render">
            <VideoForm productId={report.product.id} />
            <ul className="mt-4 space-y-3">
              {report.videos.map((job) => (
                <li key={job.id} className="rounded-xl border border-line p-3 text-sm">
                  <div className="mb-2 flex items-center justify-between">
                    <span>{job.concept.pattern.replaceAll("_", " ")}</span>
                    <StatusPill status={job.status} />
                  </div>
                  <p>{job.concept.hook}</p>
                  <p className="mt-2 text-xs text-muted">{job.concept.disclaimer}</p>
                  <p className="mt-1 text-xs text-muted">{job.note}</p>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="Creatives" eyebrow="Hooks tied to a test">
            {report.creatives.length === 0 ? <p className="text-sm text-muted">Generate these from the test once it exists.</p> : null}
            <ul className="space-y-3">
              {report.creatives.map((creative) => (
                <li key={creative.id} className="border-t border-line pt-3 text-sm leading-6">
                  <p className="text-xs uppercase tracking-[0.14em] text-muted">{creative.angle}</p>
                  <p>{creative.hook}</p>
                </li>
              ))}
            </ul>
          </Section>
        </div>

        <aside className="xl:sticky xl:top-20 xl:self-start">
          <section className="rounded-2xl border border-ink bg-elev p-5">
            <p className="text-[11px] uppercase tracking-[0.16em] text-brass">Next step</p>
            <h2 className="mt-2 font-serif text-3xl">Test this product</h2>
            <p className="mt-3 text-sm leading-6 text-muted">
              A small test with a budget and an end date. It starts in research. Profitable is locked until actual net profit clears the gate.
            </p>
            <div className="mt-4">
              {report.test ? (
                <div className="grid gap-3 text-sm">
                  <StatusPill status={report.test.status} />
                  <p>{report.test.resultSummary}</p>
                  <Link href={`/tests/${report.test.id}`} className="underline decoration-line underline-offset-4">Open the test</Link>
                  {report.creatives.length === 0 ? (
                    <form action={generateCreativesAction}>
                      <input type="hidden" name="testId" value={report.test.id} />
                      <Button type="submit" variant="quiet">Generate creatives</Button>
                    </form>
                  ) : null}
                </div>
              ) : (
                <CreateTestForm opportunityId={opportunity.id} />
              )}
            </div>
          </section>
          <section className="mt-4 rounded-2xl border border-line bg-elev p-5 text-sm leading-6">
            <h2 className="font-serif text-xl">EU checklist</h2>
            <ul className="mt-3 space-y-2 text-muted">
              <li>Supplier country: {report.supplier?.country ?? "missing"}</li>
              <li>Warehouse country: {report.supplier?.warehouseCountry ?? "missing"}</li>
              <li>Shipping days: {report.supplier?.shippingDaysMin ?? "?"}–{report.supplier?.shippingDaysMax ?? "?"}</li>
              <li>Return location: {report.supplier?.returnLocation || "missing"}</li>
              <li>VAT: {report.supplier?.vatInformation || "missing"}</li>
              <li>Documents: {report.product.productDocuments}</li>
              <li>Safety: {report.product.safetyInformation}</li>
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

function Fact({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="rounded-2xl border border-line bg-elev p-4">
      <p className="text-[11px] uppercase tracking-[0.14em] text-muted">{label}</p>
      <p className="mt-2 text-sm">{value}</p>
      <p className="mt-1 text-xs leading-5 text-muted">{note}</p>
    </div>
  );
}
