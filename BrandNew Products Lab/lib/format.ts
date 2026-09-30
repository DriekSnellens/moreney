import type { Provenance, ValueKind } from "@/types/domain";

export function eur(value: number): string {
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: "EUR",
  }).format(value);
}

export function pct(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("de-DE", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}

export function compact(value: number): string {
  return new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 }).format(value);
}

export function kindLabel(kind: ValueKind): string {
  switch (kind) {
    case "data":
      return "Data";
    case "estimate":
      return "Estimate";
    case "ai_analysis":
      return "AI analysis";
    case "user_input":
      return "User input";
  }
}

export function provenanceLabel(provenance: Provenance): string {
  switch (provenance) {
    case "demo":
      return "Demo data";
    case "live":
      return "Live";
    case "user":
      return "User input";
    case "ai":
      return "AI analysis";
  }
}
