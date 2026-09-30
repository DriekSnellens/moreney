import { PageHeader } from "@/components/kit";
import { getLabEnv } from "@/lib/env";
import { providerDirectory } from "@/providers";

export default async function SettingsPage() {
  const env = getLabEnv();
  const providers = providerDirectory(env);
  return (
    <div>
      <PageHeader
        kicker="Settings"
        title="Connections"
        description="Keys stay in the environment. This page only says whether a provider is configured. Missing providers say so. They do not invent data."
      />
      <p className="mb-6 text-sm text-muted">
        Data mode: {env.dataMode}. Workflow runner: {env.workflowRunner}.
        {env.dataMode === "live" && !env.databaseUrl
          ? " DATABASE_URL is empty, so live edits stay in memory for this process and demo records are not loaded."
          : ""}
      </p>
      <ul className="grid gap-3 md:grid-cols-2">
        {providers.map((provider) => (
          <li key={provider.id} className="rounded-2xl border border-line bg-elev p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium">{provider.name}</p>
              <span className="text-[11px] uppercase tracking-[0.14em] text-muted">{provider.mode}</span>
            </div>
            <p className="mt-2 text-sm leading-6 text-muted">{provider.message}</p>
          </li>
        ))}
      </ul>
      <section className="mt-8 max-w-2xl text-sm leading-6 text-muted">
        <h2 className="font-serif text-2xl text-ink">Operator access</h2>
        <p className="mt-2">
          Leave OPERATOR_PASSWORD empty for a local demo. When you set it, also set OPERATOR_SESSION_SECRET.
          There is no public signup and no billing.
        </p>
      </section>
    </div>
  );
}
