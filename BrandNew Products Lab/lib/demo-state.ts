import { analyzePerformance } from "@/services/analyst";
import { draftBrand } from "@/services/brand";
import { draftCreatives } from "@/services/creatives";
import { EU_OPERATOR_DEFAULTS, calculateProfit } from "@/services/profit";
import { draftVideoConcepts } from "@/services/videos";
import type {
  LabState,
  Opportunity,
  OpportunityInputs,
  Product,
  ProfitAssumptions,
  ProfitActuals,
} from "@/types/domain";

const ORG = "00000000-0000-4000-8000-000000000001";
const USER = "00000000-0000-4000-8000-000000000002";
const NICHE_DOG = "00000000-0000-4000-8000-000000000010";
const NICHE_APT = "00000000-0000-4000-8000-000000000011";
const NICHE_MIST = "00000000-0000-4000-8000-000000000012";
export const PRODUCT_DOG = "00000000-0000-4000-8000-000000000020";
export const PRODUCT_APT = "00000000-0000-4000-8000-000000000021";
export const PRODUCT_MIST = "00000000-0000-4000-8000-000000000022";
const SUPPLIER = "00000000-0000-4000-8000-000000000030";
const SP_DOG = "00000000-0000-4000-8000-000000000031";
const SP_APT = "00000000-0000-4000-8000-000000000032";
const SP_MIST = "00000000-0000-4000-8000-000000000033";
export const OPP_DOG = "00000000-0000-4000-8000-000000000040";
export const OPP_APT = "00000000-0000-4000-8000-000000000041";
export const OPP_MIST = "00000000-0000-4000-8000-000000000042";
export const TEST_DOG = "00000000-0000-4000-8000-000000000050";
const BRAND_DOG = "00000000-0000-4000-8000-000000000060";
const STORE = "00000000-0000-4000-8000-000000000061";
const CAMPAIGN = "00000000-0000-4000-8000-000000000081";
const ADSET = "00000000-0000-4000-8000-000000000082";
const AD = "00000000-0000-4000-8000-000000000083";
const ORDER = "00000000-0000-4000-8000-000000000090";
const EXPERIMENT = "00000000-0000-4000-8000-0000000000a0";

const dogAssumptions: ProfitAssumptions = {
  ...EU_OPERATOR_DEFAULTS,
  units: 18,
  orders: 18,
  retailPriceIncVat: 29.95,
  refundRate: 2 / 18,
  returnRate: 2 / 18,
  adSpend: 240,
};

const dogActuals: ProfitActuals = {
  grossRevenue: 539.1,
  refundedRevenue: 59.9,
  adSpend: 240,
  productCostTotal: 108.8,
  orders: 18,
  units: 18,
};

function inputsFor(partial: OpportunityInputs): OpportunityInputs {
  return partial;
}

export function buildDemoState(now = new Date()): LabState {
  const iso = now.toISOString();
  const day = iso.slice(0, 10);
  const earlier = new Date(now.getTime() - 3 * 24 * 60 * 60 * 1000).toISOString();

  const dogInputs = inputsFor({
    demandScore: 68,
    demandNote: "Demo series only. This is not live search volume.",
    competitionScore: 58,
    competitionNote: "Estimate from similar generic rollers. No marketplace was crawled.",
    competitionIsEstimate: true,
    supplierAvailable: true,
    euWarehouse: true,
    shippingDays: 3,
    refundRiskScore: 35,
    refundRiskNote: "A simple tool should return less often than apparel. This is still an estimate.",
    complexityScore: 18,
    complexityNote: "One SKU. No installation and no regulated claim on the demo record.",
    creativePotentialScore: 84,
    creativePotentialNote: "A car seat before and after can be shown without a testimonial.",
    audienceClarityScore: 90,
    audienceClarityNote: "Owners of long-haired dogs who drive is a person, not a category.",
    seasonalityNote: "Shedding rises in spring. The car problem is year-round.",
  });

  const aptInputs = inputsFor({
    demandScore: 61,
    demandNote: "Demo interest shape for small-flat storage. Not a sales number.",
    competitionScore: 64,
    competitionNote: "Estimate. Storage is crowded if the ad says 'storage' and clearer if it says rented rooms.",
    competitionIsEstimate: true,
    supplierAvailable: true,
    euWarehouse: true,
    shippingDays: 5,
    refundRiskScore: 48,
    refundRiskNote: "Size mismatch is the likely return reason. Measure the under-bed height before promising a fit.",
    complexityScore: 40,
    complexityNote: "Bulky parcel. Shipping cost moves the contribution more than the unit price does.",
    creativePotentialScore: 62,
    creativePotentialNote: "A room measurement is clearer than a lifestyle shot of a tidy apartment.",
    audienceClarityScore: 80,
    audienceClarityNote: "Renters in small flats who cannot drill the walls.",
    seasonalityNote: "Moving season helps. The problem is not only September.",
  });

  const mistInputs = inputsFor({
    demandScore: 40,
    demandNote: "Weak demo signal. Fragrance claims would need documents this record does not have.",
    competitionScore: 72,
    competitionNote: "Estimate. Scented products are crowded and often make claims this lab will not invent.",
    competitionIsEstimate: true,
    supplierAvailable: true,
    euWarehouse: false,
    shippingDays: 12,
    refundRiskScore: 55,
    refundRiskNote: "Scent preference is personal. Expect returns if the smell is the product.",
    complexityScore: 70,
    complexityNote: "Chemical product. Ingredients, warnings, and responsible-person details are missing.",
    creativePotentialScore: 30,
    creativePotentialNote: "A mood shot would imply a benefit we cannot source. Creative potential stays low on purpose.",
    audienceClarityScore: 45,
    audienceClarityNote: "The audience is vague once the regulated claim is removed.",
    seasonalityNote: "No seasonality is stored.",
  });

  const niches: LabState["niches"] = [
    {
      id: NICHE_DOG,
      organizationId: ORG,
      name: "Owners of long-haired dogs who struggle with car hair",
      description: "People whose passenger seat collects a coat between the walk and the next trip.",
      targetAudience: "Dog owners who drive several times a week with a long-haired dog in the car",
      coreProblem: "Dog hair on the car seat that a normal brush does not lift",
      buyingMotivation: "Make the seat usable again before someone else sits down",
      priceRangeMin: 19,
      priceRangeMax: 39,
      currency: "EUR",
      seasonality: "Heavier in shedding season, present all year",
      geographicRelevance: "Netherlands, Belgium, Germany, northern France",
      whyInteresting:
        "The job is visible in one shot, the buyer is specific, and a generic 'pet' ad would miss them.",
      provenance: "demo",
      createdAt: earlier,
      updatedAt: earlier,
    },
    {
      id: NICHE_APT,
      organizationId: ORG,
      name: "Small-apartment renters looking for compact storage",
      description: "People in rented flats who need volume without drilling, stacking boxes, or losing the floor.",
      targetAudience: "Renters in flats under about 50 square metres",
      coreProblem: "Nowhere to put out-of-season things without blocking the room",
      buyingMotivation: "Recover floor space without changing the apartment",
      priceRangeMin: 35,
      priceRangeMax: 79,
      currency: "EUR",
      seasonality: "Stronger around moving months",
      geographicRelevance: "Dense cities in the Netherlands, Germany, and Belgium",
      whyInteresting: "The constraint is the rental, not 'home organization' as a category.",
      provenance: "demo",
      createdAt: iso,
      updatedAt: iso,
    },
    {
      id: NICHE_MIST,
      organizationId: ORG,
      name: "People who want a stronger bedtime scent in a small bedroom",
      description: "A fragrance idea included to show the compliance gate. It is not a recommendation to launch.",
      targetAudience: "Adults who want a scented room at night",
      coreProblem: "A bedroom that feels stale",
      buyingMotivation: "A more pleasant room at bedtime",
      priceRangeMin: 15,
      priceRangeMax: 32,
      currency: "EUR",
      seasonality: "Not established",
      geographicRelevance: "EU, with product-safety rules that this record does not satisfy",
      whyInteresting: "It looks easy to advertise and is a bad launch until documents exist.",
      provenance: "demo",
      createdAt: earlier,
      updatedAt: earlier,
    },
  ];

  const products: Product[] = [
    {
      id: PRODUCT_DOG,
      organizationId: ORG,
      nicheId: NICHE_DOG,
      name: "Compact car dog-hair roller",
      description: "A handheld roller intended for pet hair on car upholstery. No performance percentage is claimed.",
      complianceStatus: "no_flags_found",
      complianceNotes: "Household tool on the demo record. No certification is stored, and none is claimed.",
      productDocuments: "None stored.",
      safetyInformation: "No safety sheet is stored. Do not invent one.",
      provenance: "demo",
      createdAt: earlier,
      updatedAt: earlier,
    },
    {
      id: PRODUCT_APT,
      organizationId: ORG,
      nicheId: NICHE_APT,
      name: "Under-bed drawer for small rentals",
      description: "A low rolling drawer. The fit depends on the bed height, which the page must state.",
      complianceStatus: "no_flags_found",
      complianceNotes: "Furniture-like goods. Confirm materials and packaging before a public page.",
      productDocuments: "None stored.",
      safetyInformation: "No stability test is stored.",
      provenance: "demo",
      createdAt: iso,
      updatedAt: iso,
    },
    {
      id: PRODUCT_MIST,
      organizationId: ORG,
      nicheId: NICHE_MIST,
      name: "Bedtime room mist",
      description: "A scented spray. Ingredients and warnings are not on file.",
      complianceStatus: "review_required",
      complianceNotes: "Compliance review required. Missing ingredients, warnings, and the responsible person.",
      productDocuments: "Missing.",
      safetyInformation: "Missing. Do not write a safety claim.",
      provenance: "demo",
      createdAt: earlier,
      updatedAt: earlier,
    },
  ];

  const opportunities: Opportunity[] = [
    opportunity(OPP_DOG, PRODUCT_DOG, NICHE_DOG, SP_DOG, dogAssumptions, dogActuals, dogInputs, earlier, "Remove dog hair from your car in 30 seconds."),
    opportunity(
      OPP_APT,
      PRODUCT_APT,
      NICHE_APT,
      SP_APT,
      { ...EU_OPERATOR_DEFAULTS, retailPriceIncVat: 64, productCost: 18, shippingCost: 8, units: 80, orders: 80, adSpend: 500 },
      null,
      aptInputs,
      iso,
      "Storage that fits under the bed in a rented flat, with the height printed before the price.",
    ),
    opportunity(
      OPP_MIST,
      PRODUCT_MIST,
      NICHE_MIST,
      SP_MIST,
      { ...EU_OPERATOR_DEFAULTS, retailPriceIncVat: 24, productCost: 4.2, shippingCost: 4.8, adSpend: 350 },
      null,
      mistInputs,
      earlier,
      "Do not position this until the ingredients and warnings exist. A mood line would be a claim.",
    ),
  ];

  const profit = calculateProfit(dogAssumptions, dogActuals);
  const analyst = analyzePerformance({
    metrics: [
      {
        spend: 240,
        impressions: 20000,
        clicks: 360,
        addToCarts: 22,
        purchases: 18,
        revenue: 479.2,
      },
    ],
    profit,
  });

  const dogProduct = products[0];
  const dogNiche = niches[0];
  const dogOpportunity = opportunities[0];
  const brand = draftBrand({
    id: BRAND_DOG,
    organizationId: ORG,
    opportunity: dogOpportunity,
    product: dogProduct,
    niche: dogNiche,
    now: earlier,
  });
  const creatives = draftCreatives({
    organizationId: ORG,
    product: dogProduct,
    niche: dogNiche,
    productTestId: TEST_DOG,
    now: earlier,
    provenance: "demo",
    ids: [
      "00000000-0000-4000-8000-0000000000b0",
      "00000000-0000-4000-8000-0000000000b1",
      "00000000-0000-4000-8000-0000000000b2",
      "00000000-0000-4000-8000-0000000000b3",
      "00000000-0000-4000-8000-0000000000b4",
    ],
  });
  const concept = draftVideoConcepts(dogProduct, dogNiche)[0];

  const state: LabState = {
    organization: { id: ORG, name: "Personal", createdAt: earlier, updatedAt: earlier },
    user: {
      id: USER,
      organizationId: ORG,
      email: "operator@localhost",
      name: "Operator",
      role: "operator",
      createdAt: earlier,
      updatedAt: earlier,
    },
    niches,
    products,
    variants: [
      {
        id: "00000000-0000-4000-8000-000000000023",
        productId: PRODUCT_DOG,
        sku: "AW-ROLLER-01",
        name: "Default",
        attributes: { color: "stone" },
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    suppliers: [
      {
        id: SUPPLIER,
        organizationId: ORG,
        name: "Harbourline Fulfilment (demo)",
        country: "NL",
        warehouseCountry: "NL",
        shippingDaysMin: 2,
        shippingDaysMax: 4,
        returnLocation: "Rotterdam, NL — demo address, not a real dock",
        vatInformation: "VAT handling is not confirmed on this demo record.",
        verificationStatus: "unverified",
        productDocuments: "None stored.",
        safetyInformation: "None stored.",
        provenance: "demo",
        notes: "Demo supplier. Not verified. Do not treat the name as a real company.",
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    supplierProducts: [
      link(SP_DOG, PRODUCT_DOG, 6.4, 3.1, true, 3, earlier),
      link(SP_APT, PRODUCT_APT, 18, 8, true, 5, iso),
      link(SP_MIST, PRODUCT_MIST, 4.2, 4.8, false, 12, earlier),
    ],
    trendSignals: [
      signal("00000000-0000-4000-8000-0000000000f0", NICHE_DOG, PRODUCT_DOG, "dog hair car seat", [20, 22, 21, 24, 28, 31, 36], earlier),
      signal("00000000-0000-4000-8000-0000000000f1", NICHE_APT, PRODUCT_APT, "under bed storage rental", [14, 15, 14, 16, 16, 17, 18], iso),
      signal("00000000-0000-4000-8000-0000000000f2", NICHE_MIST, PRODUCT_MIST, "bedtime room spray", [12, 11, 13, 12, 11, 10, 10], earlier),
    ],
    marketSignals: [
      {
        id: "00000000-0000-4000-8000-0000000000f3",
        organizationId: ORG,
        productId: PRODUCT_DOG,
        source: "operator estimate",
        metric: "Comparable shelf price",
        value: "€19–€39",
        note: "Estimate. Not a scrape of a marketplace.",
        isEstimate: true,
        observedAt: earlier,
        provenance: "demo",
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    competitionSignals: [
      {
        id: "00000000-0000-4000-8000-0000000000f4",
        organizationId: ORG,
        productId: PRODUCT_DOG,
        intensityScore: 58,
        note: "Moderate if the ad is generic. Lower if the ad is the car seat, not 'pets'.",
        isEstimate: true,
        provenance: "demo",
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    opportunities,
    brands: [brand],
    stores: [
      {
        id: STORE,
        organizationId: ORG,
        platform: "shopify",
        name: "Demo store",
        externalDomain: "",
        status: "demo",
        provenance: "demo",
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    storeProducts: [],
    productTests: [
      {
        id: TEST_DOG,
        organizationId: ORG,
        productId: PRODUCT_DOG,
        nicheId: NICHE_DOG,
        brandId: BRAND_DOG,
        opportunityId: OPP_DOG,
        landingPath: "/products/afterwalk-roller",
        testBudget: 250,
        currency: "EUR",
        startsOn: earlier.slice(0, 10),
        endsOn: day,
        status: "TESTING",
        kpis: ["Net profit", "Contribution after ads", "Add to cart per click"],
        resultSummary: "Demo slice. ROAS can look acceptable while net profit is negative. Not a winner.",
        actuals: dogActuals,
        assumptions: dogAssumptions,
        provenance: "demo",
        createdAt: earlier,
        updatedAt: iso,
      },
    ],
    creativeConcepts: creatives,
    creativeAssets: [],
    videoJobs: [
      {
        id: "00000000-0000-4000-8000-000000000070",
        organizationId: ORG,
        productId: PRODUCT_DOG,
        provider: "mock",
        status: "READY",
        concept,
        error: null,
        assetUrl: null,
        note: "Demo job marked ready. No video file was rendered.",
        provenance: "demo",
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    adAccounts: [
      {
        id: "00000000-0000-4000-8000-000000000080",
        organizationId: ORG,
        provider: "meta",
        externalId: "demo",
        name: "Demo Meta account",
        currency: "EUR",
        provenance: "demo",
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    campaigns: [
      {
        id: CAMPAIGN,
        organizationId: ORG,
        adAccountId: "00000000-0000-4000-8000-000000000080",
        productId: PRODUCT_DOG,
        productTestId: TEST_DOG,
        creativeConceptId: creatives[0].id,
        name: "Afterwalk — car seat problem",
        status: "draft",
        budget: 25,
        currency: "EUR",
        landingUrl: "/products/afterwalk-roller",
        tracking: {
          campaignId: CAMPAIGN,
          creativeId: creatives[0].id,
          productId: PRODUCT_DOG,
          experimentId: EXPERIMENT,
        },
        approvedAt: null,
        provenance: "demo",
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    adSets: [
      {
        id: ADSET,
        campaignId: CAMPAIGN,
        name: "NL, BE, DE drivers",
        audience: "Demo audience note: people in NL, BE, DE. Not a live Meta audience.",
        budget: 25,
        status: "draft",
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    ads: [
      {
        id: AD,
        adSetId: ADSET,
        creativeConceptId: creatives[0].id,
        name: "Hook — car seat",
        status: "draft",
        tracking: {
          campaignId: CAMPAIGN,
          creativeId: creatives[0].id,
          productId: PRODUCT_DOG,
          experimentId: EXPERIMENT,
        },
        createdAt: earlier,
        updatedAt: earlier,
      },
    ],
    adMetrics: [
      {
        id: "00000000-0000-4000-8000-000000000084",
        adId: AD,
        day,
        spend: 240,
        impressions: 20000,
        clicks: 360,
        addToCarts: 22,
        purchases: 18,
        revenue: 479.2,
        provenance: "demo",
        createdAt: iso,
        updatedAt: iso,
      },
    ],
    orders: [
      {
        id: ORDER,
        organizationId: ORG,
        storeId: STORE,
        externalId: null,
        currency: "EUR",
        grossTotal: 29.95,
        discountTotal: 0,
        vatTotal: 5.2,
        status: "paid",
        orderedAt: iso,
        campaignId: CAMPAIGN,
        creativeId: creatives[0].id,
        productId: PRODUCT_DOG,
        experimentId: EXPERIMENT,
        utm: {
          utm_campaign: CAMPAIGN,
          utm_content: creatives[0].id,
          product_id: PRODUCT_DOG,
          experiment_id: EXPERIMENT,
        },
        provenance: "demo",
        createdAt: iso,
        updatedAt: iso,
      },
    ],
    orderItems: [
      {
        id: "00000000-0000-4000-8000-000000000091",
        orderId: ORDER,
        productId: PRODUCT_DOG,
        variantId: "00000000-0000-4000-8000-000000000023",
        quantity: 1,
        unitPrice: 29.95,
        unitCogs: 6.4,
        createdAt: iso,
        updatedAt: iso,
      },
    ],
    refunds: [],
    profitEvents: [
      {
        id: "00000000-0000-4000-8000-0000000000e0",
        organizationId: ORG,
        productId: PRODUCT_DOG,
        productTestId: TEST_DOG,
        kind: "actual",
        netProfit: Number(profit.netProfit.toFixed(2)),
        occurredOn: day,
        note: "Demo actuals run through the profit model. Shipping and fees that were not supplied stay estimates.",
        provenance: "demo",
        createdAt: iso,
        updatedAt: iso,
      },
    ],
    aiRuns: [
      {
        id: "00000000-0000-4000-8000-0000000000c0",
        organizationId: ORG,
        purpose: "performance-reading",
        provider: "rules",
        inputSummary: "Demo ad metrics for the car-hair roller test.",
        output: analyst,
        provenance: "demo",
        createdAt: iso,
        updatedAt: iso,
      },
    ],
    workflowRuns: [],
    experiments: [
      {
        id: EXPERIMENT,
        organizationId: ORG,
        productTestId: TEST_DOG,
        name: "Hook A vs Hook B",
        hypothesis: "A hook about the car seat will beat a generic pet hook on click-through. This demo sample cannot decide that.",
        status: "running",
        budget: 80,
        startDate: earlier.slice(0, 10),
        endDate: null,
        result: "No result. The purchase counts are too small.",
        uncertaintyNote: "Insufficient data. Treat any gap as a hint, not a result.",
        provenance: "demo",
        createdAt: earlier,
        updatedAt: iso,
      },
    ],
    experimentVariants: [
      {
        id: "00000000-0000-4000-8000-0000000000a1",
        experimentId: EXPERIMENT,
        name: "Hook A — car seat",
        description: "Opens on hair in the passenger seat.",
        impressions: 9000,
        clicks: 190,
        purchases: 9,
        spend: 40,
        revenue: 210,
        createdAt: earlier,
        updatedAt: iso,
      },
      {
        id: "00000000-0000-4000-8000-0000000000a2",
        experimentId: EXPERIMENT,
        name: "Hook B — generic pet",
        description: "Opens on 'pet owners'.",
        impressions: 8800,
        clicks: 120,
        purchases: 6,
        spend: 40,
        revenue: 150,
        createdAt: earlier,
        updatedAt: iso,
      },
    ],
    dailyMetrics: [
      {
        id: "00000000-0000-4000-8000-0000000000d0",
        organizationId: ORG,
        day,
        productId: PRODUCT_DOG,
        spend: 40,
        revenue: 59.9,
        netProfit: Number((profit.netProfit / 6).toFixed(2)),
        orders: 2,
        opportunitiesDiscovered: 1,
        provenance: "demo",
        createdAt: iso,
        updatedAt: iso,
      },
    ],
  };

  return state;
}

function opportunity(
  id: string,
  productId: string,
  nicheId: string,
  supplierProductId: string,
  assumptions: ProfitAssumptions,
  actuals: ProfitActuals | null,
  inputs: OpportunityInputs,
  at: string,
  positioning: string,
): Opportunity {
  return {
    id,
    organizationId: ORG,
    productId,
    nicheId,
    supplierProductId,
    stage: "opportunity_hypothesis",
    assumptions,
    actuals,
    inputs,
    positioning,
    positioningKind: "estimate",
    provenance: "demo",
    createdAt: at,
    updatedAt: at,
  };
}

function link(
  id: string,
  productId: string,
  productCost: number,
  shippingCost: number,
  euWarehouse: boolean,
  leadTimeDays: number,
  at: string,
): LabState["supplierProducts"][number] {
  return {
    id,
    supplierId: SUPPLIER,
    productId,
    supplierSku: id.slice(-4),
    productCost,
    shippingCost,
    currency: "EUR",
    moq: 50,
    euWarehouse,
    leadTimeDays,
    url: "",
    provenance: "demo",
    createdAt: at,
    updatedAt: at,
  };
}

function signal(
  id: string,
  nicheId: string,
  productId: string,
  query: string,
  series: number[],
  at: string,
): LabState["trendSignals"][number] {
  return {
    id,
    organizationId: ORG,
    nicheId,
    productId,
    source: "demo-series",
    query,
    series,
    seriesNote: "Hand-built demo series so the chart and the slope have something to read. Not Google Trends.",
    observedAt: at,
    provenance: "demo",
    createdAt: at,
    updatedAt: at,
  };
}
