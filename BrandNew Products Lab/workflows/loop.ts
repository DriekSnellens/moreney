import { getRepository } from "@/lib/repository";

const globalLoop = globalThis as unknown as { __bnplLoop?: boolean };

export function startJobLoop() {
  if (globalLoop.__bnplLoop) return;
  if (process.env.npm_lifecycle_event === "build") return;
  globalLoop.__bnplLoop = true;
  const timer = setInterval(() => {
    void advanceVideoJobs();
  }, 4000);
  timer.unref?.();
}

export async function advanceVideoJobs() {
  const repo = getRepository();
  if (repo.mode !== "demo") return;
  const state = await repo.read();
  const now = Date.now();
  const due = state.videoJobs.some((job) => {
    if (job.provider !== "mock") return false;
    const age = now - new Date(job.updatedAt).getTime();
    return (job.status === "QUEUED" && age > 2000) || (job.status === "GENERATING" && age > 6000);
  });
  if (!due) return;
  await repo.update((draft) => {
    const tick = Date.now();
    for (const job of draft.videoJobs) {
      if (job.provider !== "mock") continue;
      const age = tick - new Date(job.updatedAt).getTime();
      if (job.status === "QUEUED" && age > 2000) {
        job.status = "GENERATING";
        job.updatedAt = new Date().toISOString();
        job.note = "Demo renderer is working. No external video provider was called.";
      } else if (job.status === "GENERATING" && age > 6000) {
        job.status = "READY";
        job.assetUrl = null;
        job.note = "Demo job finished. No video file was rendered.";
        job.updatedAt = new Date().toISOString();
      }
    }
  });
}
