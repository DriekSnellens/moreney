import { connection } from "next/server";
import { getRepository } from "@/lib/repository";
import type { LabState } from "@/types/domain";

export async function getLab(): Promise<LabState> {
  await connection();
  return getRepository().read();
}
