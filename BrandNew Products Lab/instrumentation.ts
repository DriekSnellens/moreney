export async function register() {
  if (process.env.NEXT_RUNTIME === "edge") return;
  if (process.env.npm_lifecycle_event === "build") return;
  const { startJobLoop } = await import("./workflows/loop");
  startJobLoop();
}
