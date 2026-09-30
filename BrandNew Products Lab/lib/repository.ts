import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { buildDemoState } from "@/lib/demo-state";
import { getLabEnv } from "@/lib/env";
import type { LabState } from "@/types/domain";

export interface LabRepository {
  mode: "demo" | "live";
  read(): Promise<LabState>;
  update(mutate: (state: LabState) => void): Promise<LabState>;
}

const globalStore = globalThis as unknown as { __bnplQueue?: Promise<unknown> };

function queue<T>(work: () => Promise<T>): Promise<T> {
  const previous = globalStore.__bnplQueue ?? Promise.resolve();
  const run = previous.then(work, work);
  globalStore.__bnplQueue = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

class FileDemoRepository implements LabRepository {
  mode = "demo" as const;
  private readonly file = path.join(process.cwd(), ".data", "demo-state.json");

  async read(): Promise<LabState> {
    return queue(async () => {
      try {
        const raw = await readFile(this.file, "utf8");
        return JSON.parse(raw) as LabState;
      } catch {
        const state = buildDemoState();
        await mkdir(path.dirname(this.file), { recursive: true });
        await writeFile(this.file, JSON.stringify(state, null, 2));
        return state;
      }
    });
  }

  async update(mutate: (state: LabState) => void): Promise<LabState> {
    return queue(async () => {
      const state = await this.readUnqueued();
      mutate(state);
      await mkdir(path.dirname(this.file), { recursive: true });
      await writeFile(this.file, JSON.stringify(state, null, 2));
      return state;
    });
  }

  private async readUnqueued(): Promise<LabState> {
    try {
      const raw = await readFile(this.file, "utf8");
      return JSON.parse(raw) as LabState;
    } catch {
      return buildDemoState();
    }
  }
}

class LiveEmptyRepository implements LabRepository {
  mode = "live" as const;
  private state: LabState | null = null;

  async read(): Promise<LabState> {
    if (this.state) return this.state;
    const now = new Date().toISOString();
    this.state = {
      ...buildDemoState(new Date()),
      niches: [],
      products: [],
      variants: [],
      suppliers: [],
      supplierProducts: [],
      trendSignals: [],
      marketSignals: [],
      competitionSignals: [],
      opportunities: [],
      brands: [],
      stores: [],
      storeProducts: [],
      productTests: [],
      creativeConcepts: [],
      creativeAssets: [],
      videoJobs: [],
      adAccounts: [],
      campaigns: [],
      adSets: [],
      ads: [],
      adMetrics: [],
      orders: [],
      orderItems: [],
      refunds: [],
      profitEvents: [],
      aiRuns: [],
      workflowRuns: [],
      experiments: [],
      experimentVariants: [],
      dailyMetrics: [],
    };
    this.state.organization.name = "Personal";
    this.state.organization.createdAt = now;
    this.state.user.createdAt = now;
    return this.state;
  }

  async update(mutate: (state: LabState) => void): Promise<LabState> {
    const state = await this.read();
    mutate(state);
    return state;
  }
}

export function getRepository(): LabRepository {
  const env = getLabEnv();
  const holder = globalThis as unknown as { __bnplRepo?: LabRepository; __bnplMode?: string };
  if (holder.__bnplRepo && holder.__bnplMode === env.dataMode) return holder.__bnplRepo;
  const repo = env.dataMode === "live" ? new LiveEmptyRepository() : new FileDemoRepository();
  holder.__bnplRepo = repo;
  holder.__bnplMode = env.dataMode;
  return repo;
}
