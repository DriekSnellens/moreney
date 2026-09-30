import { notFound } from "next/navigation";
import { PageHeader, Section, SourcePill } from "@/components/kit";
import { getLab } from "@/lib/lab";

export default async function BrandPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const state = await getLab();
  const brand = state.brands.find((item) => item.id === id);
  if (!brand) notFound();
  return (
    <div>
      <PageHeader kicker="Brand draft" title={brand.name} description={brand.tagline} actions={<SourcePill provenance={brand.provenance} />} />
      <p className="mb-6 text-sm text-muted">{brand.nameNote}</p>
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Positioning"><p className="text-sm leading-6">{brand.positioning}</p><p className="mt-3 text-sm">{brand.personality}</p></Section>
        <Section title="Look">
          <p className="text-sm leading-6">{brand.colorDirection}</p>
          <p className="mt-2 text-sm leading-6">{brand.typographyDirection}</p>
        </Section>
        <Section title="Homepage"><p className="text-sm leading-6">{brand.homepageConcept}</p></Section>
        <Section title="Product page">
          <ol className="list-decimal space-y-1 pl-5 text-sm leading-6">
            {brand.productPageStructure.map((item) => <li key={item}>{item}</li>)}
          </ol>
        </Section>
        <Section title="FAQ">
          <dl className="grid gap-3 text-sm leading-6">
            {brand.faq.map((item) => (
              <div key={item.question}>
                <dt>{item.question}</dt>
                <dd className="text-muted">{item.answer}</dd>
              </div>
            ))}
          </dl>
        </Section>
        <Section title="Trust, bundles, upsells">
          <ul className="space-y-2 text-sm leading-6">
            {brand.trustMessaging.map((item) => <li key={item}>{item}</li>)}
            {brand.bundleIdeas.map((item) => <li key={item}>{item}</li>)}
            {brand.upsellIdeas.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </Section>
      </div>
    </div>
  );
}
