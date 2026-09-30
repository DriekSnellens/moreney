import { approveCampaign } from "@/app/actions";
import { DataTable, PageHeader, SourcePill, StatusPill } from "@/components/kit";
import { Button } from "@/components/ui";
import { eur } from "@/lib/format";
import { getLabEnv } from "@/lib/env";
import { getLab } from "@/lib/lab";

export default async function AdsPage() {
  const state = await getLab();
  const env = getLabEnv();
  return (
    <div>
      <PageHeader
        kicker="Ads"
        title="Drafts, then a pause"
        description="ROAS is shown with the metrics. The decision is contribution and net profit. Approving a campaign here does not send it to Meta or TikTok."
      />
      <p className="mb-6 text-sm text-muted">
        Meta: {env.metaAccessToken && env.metaAdAccountId ? "credentials present, writes still locked" : "Provider not configured"}.
        TikTok: {env.tiktokAccessToken ? "token present, creation stays inside the lab" : "Provider not configured"}.
      </p>
      {state.campaigns.length === 0 ? (
        <p className="text-sm text-muted">No campaigns. Create a draft from a product test.</p>
      ) : (
        <div className="grid gap-4">
          {state.campaigns.map((campaign) => {
            const metrics = state.adMetrics.filter((metric) => {
              const ad = state.ads.find((item) => item.id === metric.adId);
              const adSet = state.adSets.find((item) => item.id === ad?.adSetId);
              return adSet?.campaignId === campaign.id;
            });
            const spend = metrics.reduce((sum, metric) => sum + metric.spend, 0);
            const revenue = metrics.reduce((sum, metric) => sum + metric.revenue, 0);
            const roas = spend > 0 ? revenue / spend : null;
            return (
              <article key={campaign.id} className="rounded-2xl border border-line bg-elev p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="font-serif text-2xl">{campaign.name}</h2>
                    <p className="mt-1 text-xs text-muted">{campaign.landingUrl}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusPill status={campaign.status} />
                    <SourcePill provenance={campaign.provenance} />
                  </div>
                </div>
                <p className="mt-3 text-sm">Budget {eur(campaign.budget)} / day · ROAS {roas === null ? "—" : roas.toFixed(2)} · spend {eur(spend)} · revenue {eur(revenue)}</p>
                <p className="mt-1 text-xs text-muted">Tracking ids: campaign {campaign.tracking.campaignId}, creative {campaign.tracking.creativeId}, product {campaign.tracking.productId}</p>
                {campaign.status === "draft" ? (
                  <form action={approveCampaign} className="mt-4">
                    <input type="hidden" name="campaignId" value={campaign.id} />
                    <Button type="submit" variant="quiet">Approve and keep paused</Button>
                  </form>
                ) : (
                  <p className="mt-3 text-sm text-muted">Approved locally on {campaign.approvedAt}. No ad platform was called.</p>
                )}
                {metrics.length > 0 ? (
                  <div className="mt-4">
                    <DataTable
                      columns={["Day", "Spend", "Clicks", "ATC", "Purchases", "Revenue"]}
                      rows={metrics.map((metric) => [
                        metric.day,
                        eur(metric.spend),
                        String(metric.clicks),
                        String(metric.addToCarts),
                        String(metric.purchases),
                        eur(metric.revenue),
                      ])}
                    />
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
