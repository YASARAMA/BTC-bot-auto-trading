// The optional Claude research assistant. It writes reviews and proposes experiments; it cannot
// place orders, change settings or limits, or see the frozen test set, and every proposal needs a
// person's approval before it even becomes a research version.
import { useState } from "react";
import { Bot, CircleCheck, CircleX, NotebookPen, Sparkles } from "lucide-react";
import { JobProgress } from "../../components/domain/JobProgress";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/Dialog";
import { EmptyState, Notice, SkeletonRows } from "../../components/ui/Feedback";
import { Field, Input, Select, Switch } from "../../components/ui/Form";
import { post } from "../../lib/api";
import { fmtDateTime, titleCase } from "../../lib/format";
import { useExperiments, useResearchNotes, useResearchStatus, useStrategies } from "../../lib/queries";
import type { Job, ResearchNote } from "../../lib/types";
import { useAction } from "../../lib/useAction";

export function ResearchAssistant() {
  const status = useResearchStatus();
  const st = status.data;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
        <Card title="Research assistant" icon={<Bot className="size-4" />} subtitle="Optional; independent of the risk manager">
          {!st ? (
            <SkeletonRows rows={4} />
          ) : (
            <div className="space-y-3 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={st.available ? "up" : "neutral"} dot>
                  {st.available ? "available" : "not available"}
                </Badge>
                {st.model && <Badge>{st.model}</Badge>}
                <Badge tone={st.api_key_present ? "up" : "neutral"}>API key {st.api_key_present ? "present" : "missing"}</Badge>
              </div>
              {!st.available && (
                <Notice tone="info">
                  To enable it: set <code>research.claude_enabled</code> and <code>research.model</code> in Settings, and start the backend with the
                  <code> ANTHROPIC_API_KEY</code> environment variable. Missing: {st.problems.join("; ")}.
                </Notice>
              )}
              <div>
                <div className="mb-1 font-medium">What it can never do</div>
                <ul className="list-disc space-y-0.5 pl-4 text-muted">
                  {st.limits.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </Card>
        <AssistantActions available={!!st?.available} />
      </div>
      <Notes />
    </div>
  );
}

function AssistantActions({ available }: { available: boolean }) {
  const experiments = useExperiments();
  const strategies = useStrategies();
  const [jobId, setJobId] = useState<string | null>(null);
  const [exp, setExp] = useState("");
  const [b, setB] = useState("");
  const [strategy, setStrategy] = useState("trend_following");
  const [count, setCount] = useState("3");
  const [hours, setHours] = useState("24");
  const done = (j: Job) => setJobId(j.id);
  const review = useAction(() => post<Job>("/api/research/review", { experiment_id: exp }), { onSuccess: done, invalidate: [["jobs"]] });
  const explain = useAction(() => post<Job>("/api/research/explain", { a: exp, b }), { onSuccess: done, invalidate: [["jobs"]] });
  const logs = useAction(() => post<Job>("/api/research/review-logs", { hours: Number(hours) }), { onSuccess: done, invalidate: [["jobs"]] });
  const propose = useAction(() => post<Job>("/api/research/propose", { strategy, count: Number(count) }), { onSuccess: done, invalidate: [["jobs"]] });
  const options = (experiments.data ?? []).filter((e) => e.status === "completed").map((e) => ({ value: e.id, label: `${e.label ?? e.id} (${e.kind})` }));
  return (
    <Card title="Ask the assistant" icon={<Sparkles className="size-4" />} subtitle="Each request runs as a background job; the answer is stored as a note below">
      <div className="space-y-4 text-xs">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Experiment">
            <Select value={exp} onChange={(e) => setExp(e.target.value)} options={[{ value: "", label: "Choose an experiment" }, ...options]} />
          </Field>
          <Field label="Compare with (optional)">
            <Select value={b} onChange={(e) => setB(e.target.value)} options={[{ value: "", label: "—" }, ...options]} />
          </Field>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={!available || !exp} loading={review.isPending} onClick={() => review.mutate(undefined)}>
            Review experiment
          </Button>
          <Button size="sm" disabled={!available || !exp || !b || exp === b} loading={explain.isPending} onClick={() => explain.mutate(undefined)}>
            Explain the difference
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-[1fr_100px_auto] sm:items-end">
          <Field label="Propose experiments for">
            <Select value={strategy} onChange={(e) => setStrategy(e.target.value)} options={(strategies.data ?? []).map((s) => ({ value: s.name, label: s.title }))} />
          </Field>
          <Field label="How many">
            <Input type="number" min={1} max={5} value={count} onChange={(e) => setCount(e.target.value)} />
          </Field>
          <Button size="sm" disabled={!available} loading={propose.isPending} onClick={() => propose.mutate(undefined)}>
            Propose
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-[100px_auto] sm:items-end">
          <Field label="Log hours">
            <Input type="number" min={1} max={720} value={hours} onChange={(e) => setHours(e.target.value)} />
          </Field>
          <Button size="sm" disabled={!available} loading={logs.isPending} onClick={() => logs.mutate(undefined)} className="justify-self-start">
            Review warnings and errors
          </Button>
        </div>
        {jobId && <JobProgress jobId={jobId} onClear={() => setJobId(null)} />}
      </div>
    </Card>
  );
}

function Notes() {
  const q = useResearchNotes();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const add = useAction(() => post("/api/research/notes", { title, body }), {
    success: "Note saved",
    invalidate: [["research-notes"]],
    onSuccess: () => {
      setTitle("");
      setBody("");
    },
  });
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
      <Card title="Research notes and proposals" subtitle="Assistant output and your own journal, newest first">
        {q.isLoading ? (
          <SkeletonRows rows={6} />
        ) : !q.data?.length ? (
          <EmptyState title="No notes yet">Assistant reviews, proposals awaiting your decision and journal entries appear here.</EmptyState>
        ) : (
          <div className="space-y-3">
            {q.data.map((n) => (
              <NoteCard key={n.id} n={n} />
            ))}
          </div>
        )}
      </Card>
      <Card title="Journal entry" icon={<NotebookPen className="size-4" />}>
        <div className="space-y-3">
          <Field label="Title">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
          </Field>
          <Field label="Note">
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={6}
              maxLength={20000}
              className="w-full rounded-lg border border-border-strong bg-surface-2 px-2.5 py-2 text-[13px] text-text focus:border-accent focus:outline-none"
            />
          </Field>
          <Button variant="primary" disabled={!title.trim() || !body.trim()} loading={add.isPending} onClick={() => add.mutate(undefined)}>
            Save note
          </Button>
        </div>
      </Card>
    </div>
  );
}

function NoteCard({ n }: { n: ResearchNote }) {
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  const [runValidation, setRunValidation] = useState(true);
  const decide = useAction(
    ({ note }: { note: string }) => post(`/api/research/notes/${n.id}/${decision}`, { note, run_validation: decision === "approve" && runValidation }),
    { success: decision === "approve" ? "Proposal approved and recorded as a research version" : "Proposal rejected", invalidate: [["research-notes"], ["strategies"], ["jobs"]], onSuccess: () => setDecision(null) },
  );
  const payload = n.payload as { strategy?: string; params?: Record<string, unknown>; changes?: { name: string; value: number }[]; problems?: string[] } | null;
  return (
    <article className="rounded-lg border border-border bg-surface-2/30 p-3 text-xs">
      <header className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-semibold text-text">{n.title}</span>
        <Badge tone={n.source === "claude" ? "accent" : "neutral"}>{n.source === "claude" ? "assistant" : n.source}</Badge>
        <Badge>{n.kind}</Badge>
        <Badge tone={n.status === "pending_approval" ? "warn" : n.status === "approved" ? "up" : n.status === "rejected" ? "down" : "neutral"}>{n.status.replace("_", " ")}</Badge>
        <span className="num ml-auto text-faint">{fmtDateTime(n.created_at)}</span>
      </header>
      {n.experiment_id && <div className="mt-1 text-faint">Experiment {n.experiment_id}</div>}
      <p className="mt-2 whitespace-pre-wrap leading-5 text-text/90">{n.body}</p>
      {payload?.changes && (
        <div className="mt-2 rounded-md border border-border p-2">
          <div className="mb-1 font-medium">Proposed parameter changes for {titleCase(payload.strategy ?? "")}</div>
          {payload.changes.length ? (
            <div className="flex flex-wrap gap-1.5">
              {payload.changes.map((c) => (
                <Badge key={c.name}>
                  {titleCase(c.name)} → {c.value}
                </Badge>
              ))}
            </div>
          ) : (
            <span className="text-muted">No parameter changes (re-test of the current version).</span>
          )}
          {payload.problems && payload.problems.length > 0 && <div className="mt-1 text-down">Rejected automatically: {payload.problems.join("; ")}</div>}
        </div>
      )}
      {n.decided_at && (
        <div className="mt-2 text-faint">
          Decided by {n.decided_by} at {fmtDateTime(n.decided_at)}: {n.decision_note}
        </div>
      )}
      {n.status === "pending_approval" && (
        <div className="mt-3 flex gap-2">
          <Button size="sm" variant="success" icon={<CircleCheck className="size-3.5" />} onClick={() => setDecision("approve")}>
            Approve as research version
          </Button>
          <Button size="sm" variant="ghost" icon={<CircleX className="size-3.5" />} onClick={() => setDecision("reject")}>
            Reject
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={!!decision}
        onClose={() => setDecision(null)}
        title={decision === "approve" ? "Approve this proposal?" : "Reject this proposal?"}
        confirmLabel={decision === "approve" ? "Approve" : "Reject"}
        danger={decision === "reject"}
        busy={decide.isPending}
        requireNote="Your decision note (recorded)"
        onConfirm={(note) => decide.mutate({ note })}
      >
        {decision === "approve" ? (
          <div className="space-y-2">
            <p>The proposal becomes a new strategy version in <b>research</b> status. It is not approved for trading; it must pass the validation protocol and a person&apos;s approval first.</p>
            <Switch checked={runValidation} onChange={setRunValidation} label={<span className="text-xs">Run the validation protocol right away</span>} />
          </div>
        ) : (
          <p>The proposal is kept in the record as rejected.</p>
        )}
      </ConfirmDialog>
    </article>
  );
}
