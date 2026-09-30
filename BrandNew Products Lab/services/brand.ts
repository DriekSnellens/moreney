import type { Brand, Niche, Opportunity, Product } from "@/types/domain";

export function draftBrand(input: {
  id: string;
  organizationId: string;
  opportunity: Opportunity;
  product: Product;
  niche: Niche;
  now: string;
}): Brand {
  const specific = KNOWN[input.product.id];
  const name = specific?.name ?? placeholderName(input.product.name);
  return {
    id: input.id,
    organizationId: input.organizationId,
    opportunityId: input.opportunity.id,
    name,
    nameNote: specific
      ? "Working name for this draft. Rename it before anything public."
      : "Placeholder name. Rename it before anything public.",
    positioning: specific?.positioning ?? input.opportunity.positioning,
    targetAudience: input.niche.targetAudience,
    personality: specific?.personality ?? "Plain, specific, and calm. No mascot, no fake urgency.",
    colorDirection: specific?.colorDirection ?? "Stone background, ink text, one muted metal accent.",
    typographyDirection:
      specific?.typographyDirection ?? "A readable grotesque for text. Use a serif only for the name.",
    tagline: specific?.tagline ?? `For ${input.niche.name.toLowerCase()}.`,
    homepageConcept:
      specific?.homepageConcept ??
      "One problem, one product, the price, the shipping window, and the return location. Nothing else above the fold.",
    productPageStructure: [
      "Who it is for, in one sentence",
      "The problem in the customer's words",
      "What the product does, without a performance claim you cannot source",
      "Price, shipping days, and where returns go",
      "Materials and documents you actually have",
      "FAQ",
    ],
    faq: [
      {
        question: "Where does it ship from?",
        answer: "State the warehouse country from the supplier record. Do not promise a country you have not confirmed.",
      },
      {
        question: "How long does delivery take?",
        answer: "Use the supplier's shipping range. If it is missing, say it is not confirmed yet.",
      },
      {
        question: "What is the return policy?",
        answer: "EU customers need a real withdrawal right. Write the policy you can operate, including where the return goes.",
      },
    ],
    trustMessaging: [
      "Show the supplier country and warehouse country.",
      "Show materials only when they are on the supplier record.",
      "Do not invent reviews, testimonials, or a count of customers.",
      "Do not invent certifications or safety claims.",
    ],
    bundleIdeas: specific?.bundleIdeas ?? ["A two-pack only if the landed cost still leaves contribution after ads."],
    upsellIdeas: specific?.upsellIdeas ?? ["A refill or spare only after the first product is profitable."],
    provenance: input.opportunity.provenance === "demo" ? "demo" : "user",
    createdAt: input.now,
    updatedAt: input.now,
  };
}

function placeholderName(productName: string): string {
  const word = productName.split(" ").find((part) => part.length > 3) ?? "Product";
  return `${word} Co.`;
}

const KNOWN: Record<
  string,
  {
    name: string;
    positioning: string;
    personality: string;
    colorDirection: string;
    typographyDirection: string;
    tagline: string;
    homepageConcept: string;
    bundleIdeas: string[];
    upsellIdeas: string[];
  }
> = {
  "00000000-0000-4000-8000-000000000020": {
    name: "Afterwalk",
    positioning:
      "A small tool brand for people who drive with long-haired dogs and are tired of hair in the seats.",
    personality: "Practical, dry, and calm. It does not talk like a pet influencer.",
    colorDirection: "Warm stone, ink, and one brass accent. No paw prints.",
    typographyDirection: "A plain grotesque for everything except the wordmark.",
    tagline: "The car stays calm after the walk.",
    homepageConcept:
      "Open on a car seat, name the hair problem, show the 30-second use, then price and shipping. No countdown.",
    bundleIdeas: ["A two-pack for a second car, priced only if contribution survives the discount."],
    upsellIdeas: ["A replacement head, sold after the first order proves people keep it."],
  },
  "00000000-0000-4000-8000-000000000021": {
    name: "Onderla",
    positioning: "Storage for renters who cannot drill into the walls of a small flat.",
    personality: "Quiet and practical. It respects a rented home.",
    colorDirection: "Off-white, graphite, and a pale oak accent.",
    typographyDirection: "A compact grotesque. Measurements should look like a specification, not a slogan.",
    tagline: "Storage that fits the flat you actually rent.",
    homepageConcept:
      "Lead with the floor area it uses, the height under a typical bed, and how it leaves the flat.",
    bundleIdeas: ["Two drawers only if the parcel still ships inside the stated window."],
    upsellIdeas: ["A second width, offered after the first size sells without a spike in returns."],
  },
};
