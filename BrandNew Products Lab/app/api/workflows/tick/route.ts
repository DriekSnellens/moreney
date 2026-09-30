import { advanceVideoJobs } from "@/workflows/loop";

export async function POST() {
  await advanceVideoJobs();
  return Response.json({ ok: true });
}
