"use client";

import { Command, Menu, Search } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

const links = ["Features", "Pricing", "Docs", "Changelog"];

export function Navbar() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <div className="flex items-center gap-8">
          <a href="#" className="flex items-center gap-2">
            <div className="h-7 w-7 rounded-md bg-primary" />
            <span className="font-semibold">Autoposter</span>
          </a>
          <nav className="hidden md:flex items-center gap-6">
            {links.map((l) => (
              <a
                key={l}
                href="#"
                className="text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                {l}
              </a>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="hidden md:inline-flex items-center gap-2 rounded-md border border-border bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground"
          >
            <Search className="h-3.5 w-3.5" />
            Search...
            <kbd className="ml-4 inline-flex items-center gap-0.5 rounded bg-background px-1.5 py-0.5 text-[10px]">
              <Command className="h-3 w-3" />K
            </kbd>
          </button>
          <button
            type="button"
            className="hidden md:inline-flex rounded-full bg-primary px-4 py-1.5 text-sm text-primary-foreground"
          >
            Sign in
          </button>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="md:hidden rounded-md border border-border p-2"
            aria-label="Menu"
          >
            <Menu className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div className={cn("md:hidden overflow-hidden transition-[max-height]", open ? "max-h-64" : "max-h-0")}>
        <nav className="flex flex-col gap-1 border-t border-border px-6 py-3">
          {links.map((l) => (
            <a key={l} href="#" className="py-2 text-sm text-foreground">
              {l}
            </a>
          ))}
        </nav>
      </div>
    </header>
  );
}
