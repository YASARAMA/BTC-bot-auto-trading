import { useMemo, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "../../lib/cn";
import { EmptyState, SkeletonRows } from "./Feedback";

export type Column<T> = {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  sort?: (row: T) => number | string | null | undefined;
  align?: "left" | "right" | "center";
  className?: string;
  width?: string;
};

export function DataTable<T>({ rows, columns, rowKey, loading, empty, maxHeight, initialSort, onRowClick, dense, rowClassName }: {
  rows: T[] | undefined;
  columns: Column<T>[];
  rowKey: (row: T, index: number) => string | number;
  loading?: boolean;
  empty?: ReactNode;
  maxHeight?: number;
  initialSort?: { key: string; dir: "asc" | "desc" };
  onRowClick?: (row: T) => void;
  dense?: boolean;
  rowClassName?: (row: T) => string | undefined;
}) {
  const [sort, setSort] = useState(initialSort);
  const sorted = useMemo(() => {
    if (!rows) return [];
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sort) return rows;
    const get = col.sort;
    return [...rows].sort((a, b) => {
      const va = get(a);
      const vb = get(b);
      if (va === vb) return 0;
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      const r = va < vb ? -1 : 1;
      return sort.dir === "asc" ? r : -r;
    });
  }, [rows, sort, columns]);

  if (loading && !rows) return <SkeletonRows rows={6} />;
  if (!sorted.length) return <>{empty ?? <EmptyState title="Nothing to show yet" />}</>;

  return (
    <div className="overflow-auto rounded-lg border border-border" style={maxHeight ? { maxHeight } : undefined}>
      <table className="w-full border-collapse text-left text-[13px]">
        <thead className="sticky top-0 z-10 bg-surface-2/95 backdrop-blur">
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                style={c.width ? { width: c.width } : undefined}
                className={cn(
                  "whitespace-nowrap border-b border-border px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted",
                  c.align === "right" && "text-right",
                  c.align === "center" && "text-center",
                  c.sort && "cursor-pointer select-none hover:text-text",
                )}
                onClick={() => {
                  if (!c.sort) return;
                  setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === "asc" ? "desc" : "asc" } : { key: c.key, dir: "desc" }));
                }}
              >
                <span className="inline-flex items-center gap-1">
                  {c.header}
                  {sort?.key === c.key && <ChevronDown className={cn("size-3 transition-transform", sort.dir === "asc" && "rotate-180")} />}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn("border-b border-border/60 last:border-0 hover:bg-surface-2/60", onRowClick && "cursor-pointer", rowClassName?.(row))}
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={cn("num whitespace-nowrap px-3 text-text/90", dense ? "py-1.5" : "py-2",
                    c.align === "right" && "text-right", c.align === "center" && "text-center", c.className)}
                >
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
