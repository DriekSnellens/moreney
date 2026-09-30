import type { AspectRatio, Niche, Product, VideoConcept } from "@/types/domain";

export function draftVideoConcepts(product: Product, niche: Niche, aspectRatio: AspectRatio = "9:16"): VideoConcept[] {
  const disclaimer = "AI spokesperson. Not a customer. Do not present this person as someone who bought the product.";
  return [
    {
      persona: niche.targetAudience,
      spokesperson: "An adult speaking to camera. Generated actor. Not a testimonial.",
      setting: "The place the problem actually happens.",
      hook: niche.coreProblem,
      script: `${niche.coreProblem} Here is ${product.name}, used once. ${disclaimer}`,
      demonstration: "Show the product performing the single job in the hook.",
      cta: "See the product.",
      lengthSeconds: 20,
      aspectRatio,
      language: "en",
      tone: "Calm and specific.",
      disclaimer,
      pattern: "problem_solution",
    },
    {
      persona: niche.targetAudience,
      spokesperson: "No face required. Hands only.",
      setting: "A tight shot of the product and the surface it works on.",
      hook: `Watch ${product.name} used once.`,
      script: "No story. Hands, product, result. On-screen text only states what is visible.",
      demonstration: "One uninterrupted use.",
      cta: "Details and shipping are on the page.",
      lengthSeconds: 15,
      aspectRatio,
      language: "en",
      tone: "Quiet.",
      disclaimer: "Product demonstration. No customer claim.",
      pattern: "demonstration",
    },
    {
      persona: niche.targetAudience,
      spokesperson: "Generated person, labeled on screen for the whole video.",
      setting: "An ordinary home, not a set dressed to look like an ad.",
      hook: "A generated speaker can describe the problem. They cannot say they bought it.",
      script: `On-screen label stays visible: "${disclaimer}" The line is about the problem, then the product is shown. There is no 'I love mine' and no star rating.`,
      demonstration: "Cut to the product doing the job.",
      cta: "See why the product is built for this situation.",
      lengthSeconds: 25,
      aspectRatio,
      language: "en",
      tone: "Conversational, not breathless.",
      disclaimer,
      pattern: "ugc_style",
    },
  ];
}
