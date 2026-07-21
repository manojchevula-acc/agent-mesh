import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ShieldAlert, CheckCircle2, XCircle, Clock, Loader2, ChevronDown, ChevronUp } from "lucide-react";
import { getApprovalDetails, approveRequest, rejectRequest } from "@/api/mesh";
import type { HitlDetails, LLMReasoningEntry } from "@/types/mesh";

type ApprovalDetails = HitlDetails & { approval_id: string };

type PageState =
  | { status: "loading" }
  | { status: "ready"; details: ApprovalDetails }
  | { status: "deciding"; details: ApprovalDetails }
  | { status: "not_found" }
  | { status: "approved" }
  | { status: "rejected" }
  | { status: "error"; message: string };

export default function ApprovalPage() {
  const { id } = useParams<{ id: string }>();
  const [state, setState] = useState<PageState>({ status: "loading" });

  useEffect(() => {
    if (!id) {
      setState({ status: "not_found" });
      return;
    }

    // 1. Check localStorage first — set by the originating tab for instant access.
    //    This avoids any API race condition when the tab opens before the backend
    //    serves the GET endpoint. Also handles popup blockers where the tab opens
    //    later than the SSE event.
    try {
      const stored = localStorage.getItem(`hitl-approval-${id}`);
      if (stored) {
        const parsed = JSON.parse(stored) as HitlDetails & { approval_id: string };
        localStorage.removeItem(`hitl-approval-${id}`);
        setState({ status: "ready", details: parsed });
        return;
      }
    } catch {
      // localStorage unavailable — fall through to API
    }

    // 2. Fallback: fetch from API (used when opened via email link where
    //    localStorage won't have the data).
    getApprovalDetails(id)
      .then((details) => setState({ status: "ready", details }))
      .catch((err) => {
        if (err?.response?.status === 404) {
          setState({ status: "not_found" });
        } else {
          setState({ status: "error", message: "Failed to load approval request. The request may have expired or the server is unreachable." });
        }
      });
  }, [id]);

  async function handleDecision(decision: "approve" | "reject") {
    if (state.status !== "ready") return;
    const details = state.details;
    setState({ status: "deciding", details });
    try {
      if (decision === "approve") {
        await approveRequest(details.approval_id);
        setState({ status: "approved" });
      } else {
        await rejectRequest(details.approval_id);
        setState({ status: "rejected" });
      }
    } catch {
      setState({ status: "error", message: "Failed to submit decision. The request may have already been resolved or timed out." });
    }
  }

  return (
    <div className="min-h-screen bg-canvas flex items-center justify-center p-6">
      <div className="w-full max-w-lg">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-500/15">
            <ShieldAlert className="h-5 w-5 text-amber-600 dark:text-amber-400" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-fg">Human Review Request</h1>
            <p className="text-sm text-muted">Agent Mesh — Compliance Gate</p>
          </div>
        </div>

        {state.status === "loading" && (
          <div className="flex items-center gap-2 text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">Loading approval request…</span>
          </div>
        )}

        {state.status === "not_found" && (
          <StatusCard
            icon={<Clock className="h-6 w-6 text-muted" />}
            title="Request not found"
            description="This approval request has already been resolved, expired, or does not exist."
            color="neutral"
          />
        )}

        {state.status === "error" && (
          <StatusCard
            icon={<XCircle className="h-6 w-6 text-red-500" />}
            title="Something went wrong"
            description={state.message}
            color="red"
          />
        )}

        {state.status === "approved" && (
          <StatusCard
            icon={<CheckCircle2 className="h-6 w-6 text-green-500" />}
            title="Request approved"
            description="The pipeline has been unblocked and will continue processing the request."
            color="green"
          />
        )}

        {state.status === "rejected" && (
          <StatusCard
            icon={<XCircle className="h-6 w-6 text-red-500" />}
            title="Request rejected"
            description="The request has been declined. The requester will receive a rejection message."
            color="red"
          />
        )}

        {(state.status === "ready" || state.status === "deciding") && (
          <ReadyView
            details={state.details}
            deciding={state.status === "deciding"}
            onDecide={handleDecision}
          />
        )}
      </div>
    </div>
  );
}

function ReadyView({
  details,
  deciding,
  onDecide,
}: {
  details: ApprovalDetails;
  deciding: boolean;
  onDecide: (d: "approve" | "reject") => void;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface shadow-lg overflow-hidden">
      {/* Amber banner */}
      <div className="bg-amber-50 dark:bg-amber-500/10 border-b border-line px-5 py-3">
        <p className="text-sm text-amber-800 dark:text-amber-300 font-medium">
          A credit officer request has passed automated compliance checks and requires your manual approval before the AI pipeline continues.
        </p>
      </div>

      {/* Details */}
      <dl className="divide-y divide-line text-sm">
        <DetailRow label="Requested by">
          <span className="font-medium text-fg">{details.user_name}</span>
          <span className="ml-1.5 text-muted">({details.role.replace(/_/g, " ")})</span>
        </DetailRow>
        <DetailRow label="Query">
          <span className="text-fg">{details.query}</span>
        </DetailRow>
        <DetailRow label="Compliance result">
          <code className="block text-xs font-mono bg-canvas rounded px-2 py-1.5 whitespace-pre-wrap break-words border border-line mt-0.5">
            {details.compliance_verdict}
          </code>
        </DetailRow>
        <DetailRow label="Approval ID">
          <span className="font-mono text-xs text-muted">{details.approval_id}</span>
        </DetailRow>
      </dl>

      {/* Compliance reasoning */}
      {details.compliance_reasoning && details.compliance_reasoning.length > 0 && (
        <ReasoningSection entries={details.compliance_reasoning} />
      )}

      {/* Action buttons */}
      <div className="px-5 py-4 flex gap-3 justify-end bg-canvas">
        <button
          onClick={() => onDecide("reject")}
          disabled={deciding}
          className="px-4 py-2 rounded-lg text-sm font-medium border border-red-300 dark:border-red-700 text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-50 transition-colors"
        >
          {deciding ? "Submitting…" : "Reject"}
        </button>
        <button
          onClick={() => onDecide("approve")}
          disabled={deciding}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-green-700 hover:bg-green-800 dark:bg-green-600 dark:hover:bg-green-500 text-white disabled:opacity-50 transition-colors flex items-center gap-2"
        >
          {deciding && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {deciding ? "Submitting…" : "Approve & Continue"}
        </button>
      </div>
    </div>
  );
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 px-5 py-3">
      <dt className="w-32 shrink-0 font-medium text-muted">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  );
}

function ReasoningSection({ entries }: { entries: LLMReasoningEntry[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-t border-line">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-5 py-3 text-sm font-medium text-muted hover:text-fg hover:bg-canvas/50 transition-colors"
      >
        <span>AI Compliance Reasoning</span>
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>

      {open && (
        <div className="px-5 pb-4 space-y-4">
          {entries.map((entry, i) => (
            <ReasoningEntry key={i} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

function ReasoningEntry({ entry }: { entry: LLMReasoningEntry }) {
  const d = entry.data;
  return (
    <div className="rounded-lg border border-line bg-canvas p-3 text-xs space-y-2">
      <div className="flex items-center gap-2 mb-1">
        <span className="font-semibold text-fg capitalize">{entry.phase.replace(/_/g, " ")}</span>
        <span className="text-faint">·</span>
        <span className="text-faint capitalize">{entry.agent}</span>
      </div>

      {d.decision && (
        <p className="text-fg"><span className="text-muted font-medium">Decision: </span>{d.decision}</p>
      )}

      {d.checks && d.checks.length > 0 && (
        <div>
          <p className="text-muted font-medium mb-1">Checks passed:</p>
          <ul className="space-y-0.5">
            {d.checks.map((c, i) => (
              <li key={i} className="flex gap-1.5 text-fg"><span className="text-green-500">✓</span>{c}</li>
            ))}
          </ul>
        </div>
      )}

      {d.risk_signals && d.risk_signals.length > 0 && (
        <div>
          <p className="text-muted font-medium mb-1">Risk signals:</p>
          <ul className="space-y-0.5">
            {d.risk_signals.map((r, i) => (
              <li key={i} className="flex gap-1.5 text-amber-600 dark:text-amber-400"><span>⚠</span>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {d.authorization && (
        <div className="pt-1 border-t border-line space-y-0.5">
          <p className="text-muted font-medium">Authorization:</p>
          {d.authorization.request_task_category && (
            <p className="text-fg"><span className="text-muted">Task category: </span>{d.authorization.request_task_category}</p>
          )}
          {d.authorization.authorized !== undefined && (
            <p className={d.authorization.authorized ? "text-green-600 dark:text-green-400" : "text-red-500"}>
              {d.authorization.authorized ? "✓ Authorized" : "✗ Not authorized"}
            </p>
          )}
          {d.authorization.authorization_rationale && (
            <p className="text-fg italic">{d.authorization.authorization_rationale}</p>
          )}
        </div>
      )}

      {d.rationale && (
        <p className="text-muted italic">{d.rationale}</p>
      )}
    </div>
  );
}

function StatusCard({
  icon,
  title,
  description,
  color,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  color: "green" | "red" | "neutral";
}) {
  const border = {
    green: "border-green-200 dark:border-green-800",
    red: "border-red-200 dark:border-red-800",
    neutral: "border-line",
  }[color];

  return (
    <div className={`rounded-xl border ${border} bg-surface p-6 flex items-start gap-4`}>
      <div className="shrink-0 mt-0.5">{icon}</div>
      <div>
        <h2 className="font-semibold text-fg">{title}</h2>
        <p className="mt-1 text-sm text-muted">{description}</p>
        <p className="mt-3 text-xs text-faint">You may close this tab.</p>
      </div>
    </div>
  );
}
