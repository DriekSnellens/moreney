import type { ComplianceStatus } from "@/types/domain";

export function complianceLabel(status: ComplianceStatus): string {
  switch (status) {
    case "insufficient_information":
      return "Compliance review required";
    case "review_required":
      return "Compliance review required";
    case "no_flags_found":
      return "No flags in stored data";
    case "blocked":
      return "Blocked";
  }
}

export function complianceDetail(status: ComplianceStatus): string {
  switch (status) {
    case "insufficient_information":
      return "Documents or safety information are missing. Do not launch.";
    case "review_required":
      return "A person still has to review this product. Do not launch it automatically.";
    case "no_flags_found":
      return "Nothing in the stored record raises a flag. This is not a certification or a legal opinion.";
    case "blocked":
      return "Launch and paid tests stay closed.";
  }
}
