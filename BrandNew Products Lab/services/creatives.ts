import type { CreativeAngle, CreativeConcept, Niche, Product } from "@/types/domain";

const ANGLES: CreativeAngle[] = ["Problem", "Demonstration", "Lifestyle"];

export function draftCreatives(input: {
  organizationId: string;
  product: Product;
  niche: Niche;
  productTestId: string | null;
  now: string;
  ids: string[];
  provenance: CreativeConcept["provenance"];
}): CreativeConcept[] {
  const hooks = [
    `${input.niche.coreProblem} This is the specific version of that problem.`,
    `Show the mess first. Then show the product doing one job.`,
    `If you are ${input.niche.targetAudience.toLowerCase()}, you already know the annoyance.`,
    "Do not open with a discount. Open with the situation.",
    "One claim, one demonstration, one next step.",
  ];
  const scripts = [
    `Problem: name ${input.niche.coreProblem} Then show ${input.product.name} used once. End on the product page, not a countdown.`,
    `Demonstration: hands, product, result. No voiceover claim you cannot prove. Label the speaker if the person is generated.`,
    `Lifestyle: the room or the car where this actually happens. Keep the product in frame. No stock-celebration ending.`,
  ];
  const primary = [
    `${input.product.name} for ${input.niche.name.toLowerCase()}. Shipping and returns are stated on the page, not in the ad.`,
    "Watch it used once. Then decide. There is no invented customer count.",
    input.niche.buyingMotivation,
  ];
  const headlines = [
    input.product.name,
    input.niche.coreProblem,
    "See it used once",
  ];
  const ctas = ["See the product", "Watch the short demo", "Check shipping"];
  return hooks.map((hook, index) => ({
    id: input.ids[index] ?? input.ids[0],
    organizationId: input.organizationId,
    productId: input.product.id,
    productTestId: input.productTestId,
    angle: ANGLES[index % ANGLES.length],
    hook,
    script: scripts[index % scripts.length],
    primaryText: primary[index % primary.length],
    headline: headlines[index % headlines.length],
    cta: ctas[index % ctas.length],
    disclaimer:
      "Generated draft. Do not add reviews, scarcity, or a claim that a generated person is a customer.",
    provenance: input.provenance,
    createdAt: input.now,
    updatedAt: input.now,
  }));
}
