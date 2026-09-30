import { ImportForm } from "@/components/forms";
import { DataTable, PageHeader, RecordLink, SourcePill, Sparkline } from "@/components/kit";
import { getLabEnv } from "@/lib/env";
import { getLab } from "@/lib/lab";
import { providerDirectory } from "@/providers";
import { readTrend } from "@/services/trend-math";

export default async function DiscoverPage() {
  const state = await getLab();
  const env = getLabEnv();
  const providers = providerDirectory(env).filter((item) => ["trends", "suppliers", "market"].includes(item.id));
  return (
    <div>
      <PageHeader
        kicker="Discover"
        title="Signals, then a hypothesis"
        description="A rising series is a trend signal. It becomes an opportunity only with a supplier, a price, and a cost. It is not a winning product."
      />
      <div className="mb-6 grid gap-3 md:grid-cols-3">
        {providers.map((provider) => (
          <article key={provider.id} className="rounded-2xl border border-line bg-elev p-4">
            <p className="text-sm font-medium">{provider.name}</p>
            <p className="mt-2 text-sm leading-6 text-muted">{provider.message}</p>
          </article>
        ))}
      </div>
      <section className="rounded-2xl border border-line bg-elev p-5">
        <h2 className="font-serif text-2xl">Trend series in this workspace</h2>
        {state.trendSignals.length === 0 ? (
          <p className="mt-4 text-sm text-muted">Provider not configured. No series is invented in live mode.</p>
        ) : (
          <div className="mt-4">
            <DataTable
              columns={["Query", "Series", "Reading", "Product"]}
              rows={state.trendSignals.map((signal) => {
                const reading = readTrend(signal.series);
                const opportunity = state.opportunities.find((item) => item.productId === signal.productId);
                const product = state.products.find((item) => item.id === signal.productId);
                return [
                  <span key="q">
                    {signal.query}
                    <span className="mt-1 block"><SourcePill provenance={signal.provenance} /></span>
                  </span>,
                  <Sparkline key="s" values={signal.series} />,
                  reading.note,
                  opportunity ? (
                    <RecordLink href={`/opportunities/${opportunity.id}`}>{product?.name}</RecordLink>
                  ) : (
                    product?.name ?? "—"
                  ),
                ];
              })}
            />
          </div>
        )}
      </section>
      <section className="mt-6 rounded-2xl border border-line bg-elev p-5">
        <h2 className="font-serif text-2xl">Import a hypothesis</h2>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">
          Paste supplier and price facts you already have. The lab will not fill missing trend data.
        </p>
        <div className="mt-4 max-w-2xl">
          <ImportForm />
        </div>
      </section>
    </div>
  );
}
