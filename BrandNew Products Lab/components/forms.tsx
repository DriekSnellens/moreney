"use client";

import { useActionState } from "react";
import {
  createCampaignDraft,
  createProductTest,
  createStoreDraft,
  createVideoAction,
  importOpportunity,
  moveTest,
  saveAssumptions,
} from "@/app/actions";
import { Button, Input, Textarea } from "@/components/ui";
import { eur, pct } from "@/lib/format";
import { calculateProfit } from "@/services/profit";
import type { ActionState } from "@/app/actions";
import type { AspectRatio, ProfitAssumptions, ProfitActuals, TestStatus } from "@/types/domain";
import { useState } from "react";

const FIELDS: { key: keyof ProfitAssumptions; label: string }[] = [
  { key: "units", label: "Units" },
  { key: "orders", label: "Orders" },
  { key: "retailPriceIncVat", label: "Retail price incl. VAT" },
  { key: "discountRate", label: "Discount rate" },
  { key: "vatRate", label: "VAT rate" },
  { key: "productCost", label: "Product cost / unit" },
  { key: "shippingCost", label: "Outbound shipping / unit" },
  { key: "paymentFeeRate", label: "Payment fee rate" },
  { key: "paymentFeeFixed", label: "Payment fixed fee" },
  { key: "refundRate", label: "Refund rate" },
  { key: "returnRate", label: "Return rate" },
  { key: "returnShippingCost", label: "Return shipping / unit" },
  { key: "restockRecoveryRate", label: "Restock recovery" },
  { key: "supplierFeePerUnit", label: "Supplier fee / unit" },
  { key: "operationalCostPerOrder", label: "Ops cost / order" },
  { key: "otherCosts", label: "Other costs" },
  { key: "adSpend", label: "Ad spend" },
  { key: "executionBufferRate", label: "Execution buffer" },
];

export function ProfitEditor({
  opportunityId,
  assumptions,
  actuals,
}: {
  opportunityId: string;
  assumptions: ProfitAssumptions;
  actuals: ProfitActuals | null;
}) {
  const [current, setCurrent] = useState(assumptions);
  const [state, action, pending] = useActionState<ActionState | null, FormData>(saveAssumptions, null);
  let previewError = "";
  let preview = null as ReturnType<typeof calculateProfit> | null;
  try {
    preview = calculateProfit(current, actuals);
  } catch (error) {
    previewError = error instanceof Error ? error.message : "Check the assumptions.";
  }
  return (
    <form action={action} className="grid gap-4">
      <input type="hidden" name="opportunityId" value={opportunityId} />
      <div className="grid gap-3 sm:grid-cols-2">
        {FIELDS.map((field) => (
          <label key={field.key} className="grid gap-1 text-xs text-muted">
            {field.label}
            <Input
              name={field.key}
              inputMode="decimal"
              value={current[field.key]}
              onChange={(event) =>
                setCurrent((previous) => ({
                  ...previous,
                  [field.key]: event.target.value === "" ? 0 : Number(event.target.value),
                }))
              }
            />
          </label>
        ))}
      </div>
      {previewError ? <p className="text-sm text-bad">{previewError}</p> : null}
      {preview ? (
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Result label="Gross revenue" value={eur(preview.grossRevenue)} />
          <Result label="Gross margin" value={pct(preview.grossMargin)} />
          <Result label="Contribution" value={eur(preview.contributionMargin)} />
          <Result label="Net profit" value={eur(preview.netProfit)} bad={preview.netProfit < 0} />
        </dl>
      ) : null}
      <p className="text-xs leading-5 text-muted">
        {preview?.mode === "actual"
          ? "Actual revenue, ad spend, and product cost override the matching estimates. Other lines stay estimates until you have them."
          : preview?.mode === "blended"
            ? "Mixed. Lines with recorded actuals are marked when you save. The rest are estimates."
            : "Every figure above is an estimate from the assumptions in this form. Nothing here is a sales result."}
      </p>
      {state?.error ? <p className="text-sm text-bad">{state.error}</p> : null}
      {state?.ok ? <p className="text-sm text-good">Assumptions saved.</p> : null}
      <Button type="submit" disabled={pending || Boolean(previewError)}>
        {pending ? "Saving" : "Save assumptions"}
      </Button>
    </form>
  );
}

function Result({ label, value, bad = false }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="rounded-xl border border-line px-3 py-3">
      <dt className="text-[11px] uppercase tracking-[0.14em] text-muted">{label}</dt>
      <dd className={`num mt-1 font-serif text-2xl ${bad ? "text-bad" : ""}`}>{value}</dd>
    </div>
  );
}

export function CreateTestForm({ opportunityId }: { opportunityId: string }) {
  const [state, action, pending] = useActionState<ActionState | null, FormData>(createProductTest, null);
  const today = new Date().toISOString().slice(0, 10);
  return (
    <form action={action} className="grid gap-3">
      <input type="hidden" name="opportunityId" value={opportunityId} />
      <label className="grid gap-1 text-xs text-muted">
        Test budget (EUR)
        <Input name="budget" type="number" min="1" step="1" defaultValue={150} />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="grid gap-1 text-xs text-muted">
          Start
          <Input name="startsOn" type="date" defaultValue={today} />
        </label>
        <label className="grid gap-1 text-xs text-muted">
          End
          <Input name="endsOn" type="date" defaultValue={today} />
        </label>
      </div>
      <label className="flex items-start gap-2 text-sm leading-5">
        <input name="acknowledge" type="checkbox" className="mt-1" />
        These economics are estimates until actual orders, refunds, and ad spend replace them.
      </label>
      {state?.error ? <p className="text-sm text-bad">{state.error}</p> : null}
      {state?.ok ? <p className="text-sm text-good">Test opened in research.</p> : null}
      <Button type="submit" disabled={pending}>
        {pending ? "Opening" : "Test this product"}
      </Button>
    </form>
  );
}

export function MoveTestForm({ testId, options }: { testId: string; options: TestStatus[] }) {
  const [state, action, pending] = useActionState<ActionState | null, FormData>(moveTest, null);
  if (options.length === 0) return <p className="text-sm text-muted">No further moves from this status.</p>;
  return (
    <form action={action} className="flex flex-wrap items-end gap-3">
      <input type="hidden" name="testId" value={testId} />
      <label className="grid gap-1 text-xs text-muted">
        Move to
        <select name="status" className="rounded-xl border border-line bg-bg px-3 py-2 text-sm">
          {options.map((status) => (
            <option key={status} value={status}>
              {status.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </label>
      <Button type="submit" variant="quiet" disabled={pending}>
        Update status
      </Button>
      {state?.error ? <p className="w-full text-sm text-bad">{state.error}</p> : null}
    </form>
  );
}

export function ImportForm() {
  const [state, action, pending] = useActionState<ActionState | null, FormData>(importOpportunity, null);
  return (
    <form action={action} className="grid gap-3">
      <Textarea
        name="payload"
        defaultValue={`{
  "productName": "Narrow balcony drying rack",
  "nicheName": "Renters with a balcony too narrow for a full drying rack",
  "problem": "Wet laundry with nowhere to go in a small rented flat",
  "audience": "Apartment renters with a narrow balcony",
  "productCost": 11.5,
  "shippingCost": 6,
  "retailPriceIncVat": 39,
  "supplierCountry": "NL",
  "warehouseCountry": "NL",
  "shippingDays": 4
}`}
      />
      <p className="text-xs leading-5 text-muted">
        User input. In demo mode it stays in the demo workspace and is labeled separately from the seeded records.
        Compliance starts as review required.
      </p>
      {state?.error ? <p className="text-sm text-bad">{state.error}</p> : null}
      {state?.ok ? <p className="text-sm text-good">Imported.</p> : null}
      <Button type="submit" variant="quiet" disabled={pending}>
        Import JSON
      </Button>
    </form>
  );
}

export function VideoForm({ productId }: { productId: string }) {
  const [state, action, pending] = useActionState<ActionState | null, FormData>(createVideoAction, null);
  const ratios: AspectRatio[] = ["9:16", "1:1", "4:5", "16:9"];
  return (
    <form action={action} className="grid gap-3">
      <input type="hidden" name="productId" value={productId} />
      <label className="grid gap-1 text-xs text-muted">
        Concept
        <select name="pattern" className="rounded-xl border border-line bg-bg px-3 py-2 text-sm">
          <option value="problem_solution">Problem / solution</option>
          <option value="demonstration">Product demonstration</option>
          <option value="ugc_style">UGC-style, labeled as generated</option>
        </select>
      </label>
      <label className="grid gap-1 text-xs text-muted">
        Aspect ratio
        <select name="aspectRatio" className="rounded-xl border border-line bg-bg px-3 py-2 text-sm">
          {ratios.map((ratio) => (
            <option key={ratio}>{ratio}</option>
          ))}
        </select>
      </label>
      {state?.error ? <p className="text-sm text-bad">{state.error}</p> : null}
      {state?.ok ? <p className="text-sm text-good">Video job queued.</p> : null}
      <Button type="submit" disabled={pending}>
        Create AI promo video
      </Button>
    </form>
  );
}

export function StoreDraftForm({ productId }: { productId: string }) {
  const [state, action, pending] = useActionState<ActionState | null, FormData>(createStoreDraft, null);
  return (
    <form action={action} className="grid gap-3">
      <input type="hidden" name="productId" value={productId} />
      <label className="flex items-start gap-2 text-sm leading-5">
        <input type="checkbox" name="approved" className="mt-1" />
        I approve a draft only. Do not publish it.
      </label>
      {state?.error ? <p className="text-sm text-bad">{state.error}</p> : null}
      {state?.ok ? (
        <p className="text-sm text-good">Draft stored. In demo mode Shopify is not called. Live mode sends a DRAFT, never an active product.</p>
      ) : null}
      <Button type="submit" variant="quiet" disabled={pending}>
        Create Shopify draft
      </Button>
    </form>
  );
}

export function CampaignForm({
  testId,
  creatives,
}: {
  testId: string;
  creatives: { id: string; headline: string }[];
}) {
  const [state, action, pending] = useActionState<ActionState | null, FormData>(createCampaignDraft, null);
  return (
    <form action={action} className="grid gap-3">
      <input type="hidden" name="testId" value={testId} />
      <label className="grid gap-1 text-xs text-muted">
        Campaign name
        <Input name="name" placeholder="Angle — audience" />
      </label>
      <label className="grid gap-1 text-xs text-muted">
        Daily budget (EUR)
        <Input name="budget" type="number" min="1" step="1" defaultValue={20} />
      </label>
      <label className="grid gap-1 text-xs text-muted">
        Creative
        <select name="creativeId" className="rounded-xl border border-line bg-bg px-3 py-2 text-sm">
          {creatives.map((creative) => (
            <option key={creative.id} value={creative.id}>
              {creative.headline}
            </option>
          ))}
        </select>
      </label>
      {state?.error ? <p className="text-sm text-bad">{state.error}</p> : null}
      {state?.ok ? <p className="text-sm text-good">Draft saved. It is not live and no money was spent.</p> : null}
      <Button type="submit" variant="quiet" disabled={pending || creatives.length === 0}>
        Save ad draft
      </Button>
    </form>
  );
}
