import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { cn } from "../../lib/cn";
import { NAV } from "./nav";
import { TopBar } from "./TopBar";

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);
  const current = NAV.find((n) => (n.path === "/" ? location.pathname === "/" : location.pathname.startsWith(n.path))) ?? NAV[0];

  return (
    <div className="flex h-full min-h-screen">
      {open && <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => setOpen(false)} />}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-border bg-surface/95 backdrop-blur transition-transform lg:static lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center justify-between gap-2 border-b border-border px-4">
          <div className="flex items-center gap-2.5">
            <img src="/favicon.svg" alt="" className="size-7" />
            <div className="leading-tight">
              <div className="text-[13px] font-semibold tracking-tight">Claude Adaptive</div>
              <div className="text-[11px] text-muted">Trader · research platform</div>
            </div>
          </div>
          <button type="button" className="rounded p-1 text-muted hover:text-text lg:hidden" onClick={() => setOpen(false)} aria-label="Close menu">
            <X className="size-4" />
          </button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2" aria-label="Main">
          {NAV.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              title={item.description}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors",
                  isActive ? "bg-accent/10 text-text ring-1 ring-inset ring-accent/25" : "text-muted hover:bg-surface-2 hover:text-text",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={cn("size-4 shrink-0", isActive ? "text-accent" : "text-faint group-hover:text-muted")} />
                  <span className="truncate">{item.label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-border p-3 text-[11px] leading-4 text-faint">
          Paper trading only. Every portfolio figure is simulated; no real-money order can be placed by this version.
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar title={current.label} description={current.description} onMenu={() => setOpen(true)} menuButton={<Menu className="size-5" />} />
        <main className="mx-auto w-full max-w-[1680px] flex-1 px-3 py-4 sm:px-5 lg:px-6">{children}</main>
      </div>
    </div>
  );
}
