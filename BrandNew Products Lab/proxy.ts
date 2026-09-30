import { NextResponse, type NextRequest } from "next/server";

export async function proxy(request: NextRequest) {
  const password = process.env.OPERATOR_PASSWORD;
  if (!password) return NextResponse.next();
  const secret = process.env.OPERATOR_SESSION_SECRET;
  if (!secret) {
    return new NextResponse("OPERATOR_SESSION_SECRET is required when OPERATOR_PASSWORD is set.", { status: 503 });
  }
  if (request.nextUrl.pathname.startsWith("/login")) return NextResponse.next();
  const expected = await digest(`${secret}:${password}`);
  if (request.cookies.get("bnpl_session")?.value === expected) return NextResponse.next();
  return NextResponse.redirect(new URL("/login", request.url));
}

async function digest(value: string) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
