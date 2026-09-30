import { notFound } from "next/navigation";
import { PageHeader, RecordLink, Section, SourcePill } from "@/components/kit";
import { getLab } from "@/lib/lab";

export default async function NichePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const state = await getLab();
  const niche = state.niches.find((item) => item.id === id);
  if (!niche) notFound();
  const products = state.products.filter((item) => item.nicheId === niche.id);
  return (
    <div>
      <PageHeader kicker="Niche" title={niche.name} description={niche.description} actions={<SourcePill provenance={niche.provenance} />} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Why it is interesting">
          <p className="text-sm leading-6">{niche.whyInteresting}</p>
        </Section>
        <Section title="Buyer">
          <dl className="grid gap-3 text-sm leading-6">
            <div><dt className="text-muted">Audience</dt><dd>{niche.targetAudience}</dd></div>
            <div><dt className="text-muted">Problem</dt><dd>{niche.coreProblem}</dd></div>
            <div><dt className="text-muted">Motivation</dt><dd>{niche.buyingMotivation}</dd></div>
            <div><dt className="text-muted">Season</dt><dd>{niche.seasonality}</dd></div>
            <div><dt className="text-muted">Geography</dt><dd>{niche.geographicRelevance}</dd></div>
          </dl>
        </Section>
      </div>
      <section className="mt-6">
        <h2 className="font-serif text-2xl">Products in this niche</h2>
        <ul className="mt-3 space-y-2 text-sm">
          {products.map((product) => {
            const opportunity = state.opportunities.find((item) => item.productId === product.id);
            return (
              <li key={product.id}>
                {opportunity ? <RecordLink href={`/opportunities/${opportunity.id}`}>{product.name}</RecordLink> : product.name}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
