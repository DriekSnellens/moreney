import Link from "next/link";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui";
import { kindLabel, provenanceLabel } from "@/lib/format";
import type { Provenance, TestStatus, ValueKind } from "@/types/domain";

export function PageHeader({
  kicker,
  title,
  description,
  actions,
}: {
  kicker: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-6">
      <div className="max-w-3xl">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted">{kicker}</p>
        <h1 className="mt-2 font-serif text-4xl leading-tight tracking-tight">{title}</h1>
        {description ? <p className="mt-3 max-w-2xl text-sm leading-6 text-muted">{description}</p> : null}
      </div>
      {actions}
    </header>
  );
}

export function Metric({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "good" | "bad";
}) {
  const color = tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : "text-ink";
  return (
    <div className="rounded-2xl border border-line bg-elev px-4 py-4">
      <p className="text-[11px] uppercase tracking-[0.14em] text-muted">{label}</p>
      <p className={`num mt-2 font-serif text-3xl ${color}`}>{value}</p>
      {hint ? <p className="mt-2 text-xs leading-5 text-muted">{hint}</p> : null}
    </div>
  );
}

export function SourcePill({ provenance, kind }: { provenance?: Provenance; kind?: ValueKind }) {
  if (provenance === "demo") return <Badge tone="warn">Demo data</Badge>;
  if (provenance) return <Badge tone={provenance === "ai" ? "info" : "neutral"}>{provenanceLabel(provenance)}</Badge>;
  if (kind) return <Badge tone={kind === "data" ? "good" : kind === "ai_analysis" ? "info" : "neutral"}>{kindLabel(kind)}</Badge>;
  return null;
}

export function StatusPill({ status }: { status: TestStatus | string }) {
  const tone =
    status === "PROFITABLE" || status === "SCALING"
      ? "good"
      : status === "KILLED" || status === "blocked"
        ? "bad"
        : status === "TESTING" || status === "PROMISING" || status === "READY"
          ? "info"
          : status === "review_required" || status === "PAUSED" || status === "FAILED"
            ? "warn"
            : "neutral";
  return <Badge tone={tone}>{status.replaceAll("_", " ")}</Badge>;
}

export function Section({ title, eyebrow, children }: { title: string; eyebrow?: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-elev p-5">
      {eyebrow ? <p className="text-[11px] uppercase tracking-[0.16em] text-muted">{eyebrow}</p> : null}
      <h2 className="mt-1 font-serif text-2xl tracking-tight">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <span className="text-xs text-muted">No series</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = 128;
  const height = 40;
  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - 4 - ((value - min) / (max - min || 1)) * (height - 8);
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-10 w-32 text-accent" aria-hidden>
      <polyline fill="none" stroke="currentColor" strokeWidth="1.75" points={points} />
    </svg>
  );
}

export function DataTable({ columns, rows }: { columns: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-[11px] uppercase tracking-[0.14em] text-muted">
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-line/80 last:border-0">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-3 py-3 align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-6 text-muted">{children}</p>;
}

export function RecordLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="font-medium underline decoration-line underline-offset-4 hover:decoration-ink">
      {children}
    </Link>
  );
}
