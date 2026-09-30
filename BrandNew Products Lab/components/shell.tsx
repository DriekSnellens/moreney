"use client";

import {
  ChartLine,
  Clapperboard,
  Compass,
  FlaskConical,
  Layers,
  LayoutDashboard,
  Megaphone,
  Moon,
  Package,
  Palette,
  PenLine,
  Receipt,
  Scale,
  Search,
  Settings,
  Sparkles,
  Store,
  Sun,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { NAV } from "@/config/navigation";
import { cn } from "@/lib/utils";

const ICONS = {
  "/": LayoutDashboard,
  "/discover": Compass,
  "/niches": Layers,
  "/products": Package,
  "/opportunities": Sparkles,
  "/tests": FlaskConical,
  "/brands": Palette,
  "/stores": Store,
  "/creatives": PenLine,
  "/videos": Clapperboard,
  "/ads": Megaphone,
  "/orders": Receipt,
  "/profit": Scale,
  "/analytics": ChartLine,
  "/settings": Settings,
} as const;

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-elev/80 px-3 py-5 lg:flex">
      <Link href="/" className="px-3">
        <p className="text-[11px] uppercase tracking-[0.22em] text-brass">BNPL</p>
        <p className="mt-1 font-serif text-xl leading-tight">BrandNew Products Lab</p>
      </Link>
      <nav className="mt-8 flex flex-1 flex-col gap-0.5 overflow-y-auto">
        {NAV.map((item) => {
          const Icon = ICONS[item.href];
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-muted hover:bg-bg hover:text-ink",
                active && "bg-bg text-ink",
              )}
            >
              <Icon size={16} strokeWidth={1.75} />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <p className="px-3 pt-4 text-[11px] leading-5 text-muted">One operator. Net profit after the full cost stack.</p>
    </aside>
  );
}

export function ThemeToggle() {
  return (
    <button
      type="button"
      className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-line"
      aria-label="Toggle color theme"
      onClick={() => {
        const next = !document.documentElement.classList.contains("dark");
        document.documentElement.classList.toggle("dark", next);
        localStorage.setItem("bnpl-theme", next ? "dark" : "light");
      }}
    >
      <Sun size={16} className="hidden dark:block" />
      <Moon size={16} className="block dark:hidden" />
    </button>
  );
}

export function CommandPalette({ items }: { items: { href: string; label: string; group: string }[] }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
      if (event.key === "Escape") setOpen(false);
    };
    const openFromButton = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("bnpl-command", openFromButton);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("bnpl-command", openFromButton);
    };
  }, []);
  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((item) => item.label.toLowerCase().includes(needle)).slice(0, 12);
  }, [items, query]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 bg-ink/40 p-4" onClick={() => setOpen(false)}>
      <div
        className="mx-auto mt-24 max-w-xl overflow-hidden rounded-2xl border border-line bg-elev shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-4">
          <Search size={16} className="text-muted" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Jump to a page or opportunity"
            className="w-full bg-transparent py-4 text-sm outline-none"
          />
        </div>
        <ul className="max-h-80 overflow-y-auto p-2">
          {results.map((item) => (
            <li key={`${item.group}-${item.href}-${item.label}`}>
              <Link
                href={item.href}
                onClick={() => setOpen(false)}
                className="flex items-center justify-between rounded-xl px-3 py-2 text-sm hover:bg-bg"
              >
                <span>{item.label}</span>
                <span className="text-[11px] uppercase tracking-[0.14em] text-muted">{item.group}</span>
              </Link>
            </li>
          ))}
          {results.length === 0 ? <li className="px-3 py-6 text-sm text-muted">Nothing matches.</li> : null}
        </ul>
      </div>
    </div>
  );
}

export function OpenCommandButton() {
  return (
    <button
      type="button"
      className="hidden items-center gap-2 rounded-full border border-line px-3 py-1.5 text-xs text-muted sm:inline-flex"
      onClick={() => window.dispatchEvent(new Event("bnpl-command"))}
    >
      <Search size={14} />
      Search
      <span className="rounded border border-line px-1">⌘K</span>
    </button>
  );
}
