import { getLabEnv } from "@/lib/env";

export async function GET() {
  const env = getLabEnv();
  return Response.json({
    ok: true,
    mode: env.dataMode,
    workflowRunner: env.workflowRunner,
  });
}
