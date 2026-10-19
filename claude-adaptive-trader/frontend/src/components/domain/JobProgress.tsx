import { useEffect, useRef } from "react";
import { CircleCheck, CircleX, LoaderCircle } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { post } from "../../lib/api";
import { useJobs } from "../../lib/queries";
import type { Job } from "../../lib/types";
import { Button } from "../ui/Button";

// Follows one background job until it finishes, then hands its result to `onDone`.
export function JobProgress({ jobId, onDone, onClear }: { jobId: string; onDone?: (job: Job) => void; onClear?: () => void }) {
  const jobs = useJobs();
  const qc = useQueryClient();
  const job = jobs.data?.find((j) => j.id === jobId);
  const reported = useRef<string | null>(null);
  useEffect(() => {
    if (!job || reported.current === job.id) return;
    if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
      reported.current = job.id;
      qc.invalidateQueries({ queryKey: ["experiments"] });
      qc.invalidateQueries({ queryKey: ["strategies"] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      onDone?.(job);
    }
  }, [job, onDone, qc]);
  if (!job) return null;
  const running = job.status === "queued" || job.status === "running";
  return (
    <div className="rounded-lg border border-border bg-surface-2/50 p-3 text-xs">
      <div className="flex items-center gap-2">
        {running ? <LoaderCircle className="size-4 animate-spin text-accent" /> : job.status === "completed" ? <CircleCheck className="size-4 text-up" /> : <CircleX className="size-4 text-down" />}
        <span className="min-w-0 flex-1 truncate font-medium text-text">{job.label}</span>
        <span className="num text-muted">{Math.round(job.progress * 100)}%</span>
        {running ? (
          <Button size="sm" variant="ghost" onClick={() => post(`/api/jobs/${job.id}/cancel`).catch(() => undefined)}>
            Cancel
          </Button>
        ) : (
          onClear && (
            <Button size="sm" variant="ghost" onClick={onClear}>
              Dismiss
            </Button>
          )
        )}
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3">
        <div className={`h-full rounded-full transition-all ${job.status === "failed" ? "bg-down" : job.status === "completed" ? "bg-up" : "bg-accent"}`} style={{ width: `${Math.max(3, job.progress * 100)}%` }} />
      </div>
      <div className="mt-1.5 break-words text-muted">{job.error ?? job.message}</div>
    </div>
  );
}
