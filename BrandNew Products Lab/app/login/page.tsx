import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Button, Input } from "@/components/ui";

async function digest(value: string) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function signIn(formData: FormData) {
  "use server";
  const password = process.env.OPERATOR_PASSWORD;
  const secret = process.env.OPERATOR_SESSION_SECRET;
  if (!password || !secret) redirect("/");
  const submitted = String(formData.get("password") ?? "");
  if (submitted !== password) redirect("/login?error=1");
  const jar = await cookies();
  jar.set("bnpl_session", await digest(`${secret}:${password}`), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
  });
  redirect("/");
}

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const query = await searchParams;
  return (
    <form action={signIn} className="mx-auto mt-16 max-w-sm rounded-2xl border border-line bg-elev p-6">
      <h1 className="font-serif text-3xl">Operator</h1>
      <p className="mt-2 text-sm text-muted">This lab is for one person. There is no account signup.</p>
      <label className="mt-5 grid gap-1 text-xs text-muted">
        Password
        <Input type="password" name="password" autoComplete="current-password" />
      </label>
      {query.error ? <p className="mt-3 text-sm text-bad">That password did not match.</p> : null}
      <Button type="submit" className="mt-4">Enter</Button>
    </form>
  );
}
