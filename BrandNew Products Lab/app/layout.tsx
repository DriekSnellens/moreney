import type { Metadata } from "next";
import { Fraunces, Geist, Geist_Mono } from "next/font/google";
import { NAV } from "@/config/navigation";
import { CommandPalette, OpenCommandButton, Sidebar, ThemeToggle } from "@/components/shell";
import { getLabEnv } from "@/lib/env";
import { getLab } from "@/lib/lab";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "BrandNew Products Lab",
  description: "Private lab for finding, testing, and measuring net-profitable European product opportunities.",
};

const themeScript = `
try {
  var stored = localStorage.getItem("bnpl-theme");
  var dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", dark);
} catch (e) {}
`;

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const env = getLabEnv();
  const state = await getLab();
  const commands = [
    ...NAV.map((item) => ({ href: item.href, label: item.label, group: "Navigate" })),
    ...state.opportunities.map((opportunity) => {
      const product = state.products.find((item) => item.id === opportunity.productId);
      return {
        href: `/opportunities/${opportunity.id}`,
        label: product?.name ?? "Opportunity",
        group: "Opportunities",
      };
    }),
  ];

  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${fraunces.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full bg-bg text-ink">
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="min-w-0 flex-1">
            <header className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-line bg-bg/90 px-4 py-3 backdrop-blur lg:px-8">
              <details className="relative lg:hidden">
                <summary className="cursor-pointer list-none font-serif text-lg">Menu</summary>
                <div className="absolute left-0 top-full z-30 mt-3 w-56 rounded-2xl border border-line bg-elev p-2 shadow-lg">
                  {NAV.map((item) => (
                    <a key={item.href} href={item.href} className="block rounded-xl px-3 py-2 text-sm hover:bg-bg">
                      {item.label}
                    </a>
                  ))}
                </div>
              </details>
              <p className="hidden text-sm text-muted lg:block">Find, validate, test, then keep what clears net profit.</p>
              <div className="flex items-center gap-2">
                <OpenCommandButton />
                <ThemeToggle />
              </div>
            </header>
            {env.dataMode === "demo" ? (
              <div className="border-b border-warn/30 bg-warn/10 px-4 py-2 text-sm text-warn lg:px-8">
                Demo data. These products, suppliers, orders, and metrics are fictional and are not mixed with a live workspace.
              </div>
            ) : (
              <div className="border-b border-line px-4 py-2 text-sm text-muted lg:px-8">
                Live mode. Unconfigured providers stay empty. Demo records are not loaded.
              </div>
            )}
            <main className="px-4 py-6 lg:px-8 lg:py-8">{children}</main>
          </div>
        </div>
        <CommandPalette items={commands} />
      </body>
    </html>
  );
}
