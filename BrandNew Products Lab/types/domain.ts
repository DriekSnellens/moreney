export const PROVENANCE = ["demo", "live", "user", "ai"] as const;
export type Provenance = (typeof PROVENANCE)[number];

export const EVIDENCE_STAGES = [
  "trend_signal",
  "opportunity_hypothesis",
  "validated_product",
  "profitable_product",
  "scaling_candidate",
] as const;
export type EvidenceStage = (typeof EVIDENCE_STAGES)[number];

export const TEST_STATUSES = [
  "RESEARCH",
  "VALIDATION",
  "READY_TO_TEST",
  "TESTING",
  "PROMISING",
  "PROFITABLE",
  "SCALING",
  "KILLED",
  "PAUSED",
] as const;
export type TestStatus = (typeof TEST_STATUSES)[number];

export const COMPLIANCE_STATUSES = [
  "insufficient_information",
  "review_required",
  "no_flags_found",
  "blocked",
] as const;
export type ComplianceStatus = (typeof COMPLIANCE_STATUSES)[number];

export const VALUE_KINDS = ["data", "estimate", "ai_analysis", "user_input"] as const;
export type ValueKind = (typeof VALUE_KINDS)[number];

export const VIDEO_STATUSES = ["QUEUED", "GENERATING", "READY", "FAILED"] as const;
export type VideoStatus = (typeof VIDEO_STATUSES)[number];

export const ASPECT_RATIOS = ["9:16", "1:1", "4:5", "16:9"] as const;
export type AspectRatio = (typeof ASPECT_RATIOS)[number];

export const CREATIVE_ANGLES = [
  "Problem",
  "Demonstration",
  "Before/After",
  "Education",
  "Comparison",
  "Lifestyle",
  "Curiosity",
] as const;
export type CreativeAngle = (typeof CREATIVE_ANGLES)[number];

export interface Organization {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
}

export interface User {
  id: string;
  organizationId: string;
  email: string;
  name: string;
  role: "operator";
  createdAt: string;
  updatedAt: string;
}

export interface Niche {
  id: string;
  organizationId: string;
  name: string;
  description: string;
  targetAudience: string;
  coreProblem: string;
  buyingMotivation: string;
  priceRangeMin: number;
  priceRangeMax: number;
  currency: "EUR";
  seasonality: string;
  geographicRelevance: string;
  whyInteresting: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface Product {
  id: string;
  organizationId: string;
  nicheId: string;
  name: string;
  description: string;
  complianceStatus: ComplianceStatus;
  complianceNotes: string;
  productDocuments: string;
  safetyInformation: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface ProductVariant {
  id: string;
  productId: string;
  sku: string;
  name: string;
  attributes: Record<string, string>;
  createdAt: string;
  updatedAt: string;
}

export interface Supplier {
  id: string;
  organizationId: string;
  name: string;
  country: string;
  warehouseCountry: string;
  shippingDaysMin: number | null;
  shippingDaysMax: number | null;
  returnLocation: string;
  vatInformation: string;
  verificationStatus: "unverified" | "documents_received" | "verified";
  productDocuments: string;
  safetyInformation: string;
  provenance: Provenance;
  notes: string;
  createdAt: string;
  updatedAt: string;
}

export interface SupplierProduct {
  id: string;
  supplierId: string;
  productId: string;
  supplierSku: string;
  productCost: number;
  shippingCost: number;
  currency: "EUR";
  moq: number | null;
  euWarehouse: boolean;
  leadTimeDays: number | null;
  url: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface TrendSignal {
  id: string;
  organizationId: string;
  nicheId: string | null;
  productId: string | null;
  source: string;
  query: string;
  series: number[];
  seriesNote: string;
  observedAt: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface MarketSignal {
  id: string;
  organizationId: string;
  productId: string;
  source: string;
  metric: string;
  value: string;
  note: string;
  isEstimate: boolean;
  observedAt: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface CompetitionSignal {
  id: string;
  organizationId: string;
  productId: string;
  intensityScore: number;
  note: string;
  isEstimate: boolean;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface ProfitAssumptions {
  currency: "EUR";
  units: number;
  orders: number;
  retailPriceIncVat: number;
  discountRate: number;
  vatRate: number;
  productCost: number;
  shippingCost: number;
  paymentFeeRate: number;
  paymentFeeFixed: number;
  refundRate: number;
  returnRate: number;
  returnShippingCost: number;
  restockRecoveryRate: number;
  supplierFeePerUnit: number;
  operationalCostPerOrder: number;
  otherCosts: number;
  adSpend: number;
  executionBufferRate: number;
}

export interface ProfitActuals {
  grossRevenue?: number;
  refundedRevenue?: number;
  vat?: number;
  productCostTotal?: number;
  shippingTotal?: number;
  paymentFees?: number;
  returnCosts?: number;
  supplierFees?: number;
  operationalCosts?: number;
  otherCosts?: number;
  adSpend?: number;
  units?: number;
  orders?: number;
}

export interface ProfitLine {
  key: string;
  label: string;
  amount: number;
  kind: ValueKind;
  note: string;
}

export interface ProfitResult {
  currency: "EUR";
  lines: ProfitLine[];
  grossRevenue: number;
  refundedRevenue: number;
  netSales: number;
  vat: number;
  netRevenueExVat: number;
  cogs: number;
  shipping: number;
  paymentFees: number;
  returnCosts: number;
  supplierFees: number;
  grossProfit: number;
  grossMargin: number | null;
  contributionMargin: number;
  contributionMarginRate: number | null;
  contributionAfterAds: number;
  operationalCosts: number;
  executionBuffer: number;
  adSpend: number;
  otherCosts: number;
  netProfit: number;
  netMargin: number | null;
  mode: "estimated" | "blended" | "actual";
  assumptions: ProfitAssumptions;
}

export interface OpportunityInputs {
  demandScore: number;
  demandNote: string;
  competitionScore: number;
  competitionNote: string;
  competitionIsEstimate: boolean;
  supplierAvailable: boolean;
  euWarehouse: boolean;
  shippingDays: number | null;
  refundRiskScore: number;
  refundRiskNote: string;
  complexityScore: number;
  complexityNote: string;
  creativePotentialScore: number;
  creativePotentialNote: string;
  audienceClarityScore: number;
  audienceClarityNote: string;
  seasonalityNote: string;
}

export interface FactorScore {
  key: string;
  label: string;
  score: number;
  weight: number;
  rationale: string;
  kind: ValueKind;
}

export interface Opportunity {
  id: string;
  organizationId: string;
  productId: string;
  nicheId: string;
  supplierProductId: string | null;
  stage: EvidenceStage;
  assumptions: ProfitAssumptions;
  actuals: ProfitActuals | null;
  inputs: OpportunityInputs;
  positioning: string;
  positioningKind: ValueKind;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface Brand {
  id: string;
  organizationId: string;
  opportunityId: string;
  name: string;
  nameNote: string;
  positioning: string;
  targetAudience: string;
  personality: string;
  colorDirection: string;
  typographyDirection: string;
  tagline: string;
  homepageConcept: string;
  productPageStructure: string[];
  faq: { question: string; answer: string }[];
  trustMessaging: string[];
  bundleIdeas: string[];
  upsellIdeas: string[];
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface Store {
  id: string;
  organizationId: string;
  platform: "shopify";
  name: string;
  externalDomain: string;
  status: "not_connected" | "configured" | "demo";
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface StoreProduct {
  id: string;
  storeId: string;
  productId: string;
  brandId: string | null;
  externalId: string | null;
  status: "draft" | "pending_approval" | "pushed_draft";
  seoTitle: string;
  seoDescription: string;
  bodyHtml: string;
  approvedAt: string | null;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface ProductTest {
  id: string;
  organizationId: string;
  productId: string;
  nicheId: string;
  brandId: string | null;
  opportunityId: string;
  landingPath: string;
  testBudget: number;
  currency: "EUR";
  startsOn: string;
  endsOn: string;
  status: TestStatus;
  kpis: string[];
  resultSummary: string;
  actuals: ProfitActuals | null;
  assumptions: ProfitAssumptions;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface CreativeConcept {
  id: string;
  organizationId: string;
  productId: string;
  productTestId: string | null;
  angle: CreativeAngle;
  hook: string;
  script: string;
  primaryText: string;
  headline: string;
  cta: string;
  disclaimer: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface CreativeAsset {
  id: string;
  conceptId: string;
  kind: "image" | "video" | "copy";
  r2Key: string | null;
  url: string | null;
  mimeType: string | null;
  note: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface VideoConcept {
  persona: string;
  spokesperson: string;
  setting: string;
  hook: string;
  script: string;
  demonstration: string;
  cta: string;
  lengthSeconds: number;
  aspectRatio: AspectRatio;
  language: string;
  tone: string;
  disclaimer: string;
  pattern: "problem_solution" | "demonstration" | "ugc_style";
}

export interface VideoJob {
  id: string;
  organizationId: string;
  productId: string;
  provider: "mock" | "creatify" | "heygen";
  status: VideoStatus;
  concept: VideoConcept;
  error: string | null;
  assetUrl: string | null;
  note: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface AdAccount {
  id: string;
  organizationId: string;
  provider: "meta" | "tiktok";
  externalId: string;
  name: string;
  currency: "EUR";
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface Campaign {
  id: string;
  organizationId: string;
  adAccountId: string | null;
  productId: string;
  productTestId: string | null;
  creativeConceptId: string | null;
  name: string;
  status: "draft" | "approved_paused";
  budget: number;
  currency: "EUR";
  landingUrl: string;
  tracking: TrackingIds;
  approvedAt: string | null;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface AdSet {
  id: string;
  campaignId: string;
  name: string;
  audience: string;
  budget: number;
  status: "draft" | "approved_paused";
  createdAt: string;
  updatedAt: string;
}

export interface Ad {
  id: string;
  adSetId: string;
  creativeConceptId: string | null;
  name: string;
  status: "draft" | "approved_paused";
  tracking: TrackingIds;
  createdAt: string;
  updatedAt: string;
}

export interface AdMetric {
  id: string;
  adId: string;
  day: string;
  spend: number;
  impressions: number;
  clicks: number;
  addToCarts: number;
  purchases: number;
  revenue: number;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface TrackingIds {
  campaignId: string;
  creativeId: string;
  productId: string;
  experimentId: string | null;
}

export interface OrderRecord {
  id: string;
  organizationId: string;
  storeId: string | null;
  externalId: string | null;
  currency: "EUR";
  grossTotal: number;
  discountTotal: number;
  vatTotal: number;
  status: "paid" | "refunded" | "partially_refunded";
  orderedAt: string;
  campaignId: string | null;
  creativeId: string | null;
  productId: string | null;
  experimentId: string | null;
  utm: Record<string, string>;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface OrderItem {
  id: string;
  orderId: string;
  productId: string;
  variantId: string | null;
  quantity: number;
  unitPrice: number;
  unitCogs: number;
  createdAt: string;
  updatedAt: string;
}

export interface Refund {
  id: string;
  orderId: string;
  amount: number;
  reason: string;
  refundedAt: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface ProfitEvent {
  id: string;
  organizationId: string;
  productId: string;
  productTestId: string | null;
  kind: "estimate" | "actual";
  netProfit: number;
  occurredOn: string;
  note: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface AiRun {
  id: string;
  organizationId: string;
  purpose: string;
  provider: string;
  inputSummary: string;
  output: AnalystReport | Record<string, unknown>;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface WorkflowRun {
  id: string;
  organizationId: string;
  name: string;
  status: "queued" | "running" | "succeeded" | "failed";
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: string | null;
  startedAt: string;
  finishedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface Experiment {
  id: string;
  organizationId: string;
  productTestId: string;
  name: string;
  hypothesis: string;
  status: "draft" | "running" | "ended";
  budget: number;
  startDate: string | null;
  endDate: string | null;
  result: string;
  uncertaintyNote: string;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface ExperimentVariant {
  id: string;
  experimentId: string;
  name: string;
  description: string;
  impressions: number;
  clicks: number;
  purchases: number;
  spend: number;
  revenue: number;
  createdAt: string;
  updatedAt: string;
}

export interface DailyMetric {
  id: string;
  organizationId: string;
  day: string;
  productId: string | null;
  spend: number;
  revenue: number;
  netProfit: number;
  orders: number;
  opportunitiesDiscovered: number;
  provenance: Provenance;
  createdAt: string;
  updatedAt: string;
}

export interface AnalystReport {
  whatHappened: string[];
  possibleExplanations: string[];
  whatToTestNext: string[];
  uncertainty: string;
  roas: number | null;
  netProfit: number | null;
  sufficientForCausality: false;
}

export interface LabState {
  organization: Organization;
  user: User;
  niches: Niche[];
  products: Product[];
  variants: ProductVariant[];
  suppliers: Supplier[];
  supplierProducts: SupplierProduct[];
  trendSignals: TrendSignal[];
  marketSignals: MarketSignal[];
  competitionSignals: CompetitionSignal[];
  opportunities: Opportunity[];
  brands: Brand[];
  stores: Store[];
  storeProducts: StoreProduct[];
  productTests: ProductTest[];
  creativeConcepts: CreativeConcept[];
  creativeAssets: CreativeAsset[];
  videoJobs: VideoJob[];
  adAccounts: AdAccount[];
  campaigns: Campaign[];
  adSets: AdSet[];
  ads: Ad[];
  adMetrics: AdMetric[];
  orders: OrderRecord[];
  orderItems: OrderItem[];
  refunds: Refund[];
  profitEvents: ProfitEvent[];
  aiRuns: AiRun[];
  workflowRuns: WorkflowRun[];
  experiments: Experiment[];
  experimentVariants: ExperimentVariant[];
  dailyMetrics: DailyMetric[];
}

export interface ProviderStatus {
  id: string;
  name: string;
  configured: boolean;
  mode: "live" | "demo" | "unconfigured";
  message: string;
}

export interface Unavailable {
  ok: false;
  message: "Provider not configured";
  providerId: string;
}
