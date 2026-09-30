import { VideoForm } from "@/components/forms";
import { PageHeader, SourcePill, StatusPill } from "@/components/kit";
import { getLab } from "@/lib/lab";

export default async function VideosPage() {
  const state = await getLab();
  return (
    <div>
      <PageHeader
        kicker="AI videos"
        title="Spokesperson concepts"
        description="A generated person can describe a problem. They cannot claim to be a customer. Demo jobs finish without a video file."
      />
      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        <section className="rounded-2xl border border-line bg-elev p-5">
          <h2 className="font-serif text-xl">New job</h2>
          <div className="mt-4 grid gap-3">
            {state.products.map((product) => (
              <div key={product.id} className="border-t border-line pt-3">
                <p className="mb-2 text-sm">{product.name}</p>
                <VideoForm productId={product.id} />
              </div>
            ))}
            {state.products.length === 0 ? <p className="text-sm text-muted">Add a product first.</p> : null}
          </div>
        </section>
        <ul className="grid gap-3">
          {state.videoJobs.map((job) => {
            const product = state.products.find((item) => item.id === job.productId);
            return (
              <li key={job.id} className="rounded-2xl border border-line bg-elev p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="font-serif text-2xl">{product?.name}</h2>
                  <div className="flex gap-2">
                    <StatusPill status={job.status} />
                    <SourcePill provenance={job.provenance} />
                  </div>
                </div>
                <p className="mt-3 text-sm">{job.concept.hook}</p>
                <p className="mt-2 text-sm leading-6 text-muted">{job.concept.script}</p>
                <p className="mt-3 text-xs text-muted">
                  {job.concept.aspectRatio} · {job.concept.lengthSeconds}s · {job.concept.language} · {job.provider}
                </p>
                <p className="mt-2 text-xs text-muted">{job.concept.disclaimer}</p>
                <p className="mt-2 text-xs">{job.note}</p>
                {job.assetUrl ? <p className="mt-2 text-xs">File: {job.assetUrl}</p> : <p className="mt-2 text-xs text-muted">No media file stored.</p>}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
