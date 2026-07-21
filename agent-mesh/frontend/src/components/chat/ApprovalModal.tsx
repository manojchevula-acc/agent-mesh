import { ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { HitlDetails } from "@/types/mesh";

interface ApprovalModalProps {
  open: boolean;
  approvalId: string;
  details: HitlDetails;
  loading?: boolean;
  onApprove: () => void;
  onReject: () => void;
}

export function ApprovalModal({
  open,
  approvalId,
  details,
  loading,
  onApprove,
  onReject,
}: ApprovalModalProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop — no click-to-dismiss; reviewer must make an explicit decision */}
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" aria-hidden />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="hitl-dialog-title"
        className="relative w-full max-w-lg animate-scale-in rounded-xl border border-line bg-surface p-6 shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-start gap-3 mb-5">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-500/15">
            <ShieldAlert className="h-5 w-5 text-amber-600 dark:text-amber-400" />
          </div>
          <div>
            <h3
              id="hitl-dialog-title"
              className="text-base font-semibold text-fg"
            >
              Human Review Required
            </h3>
            <p className="mt-0.5 text-sm text-muted">
              A credit officer request has passed compliance and is awaiting
              your approval before the pipeline continues.
            </p>
          </div>
        </div>

        {/* Detail rows */}
        <dl className="space-y-3 rounded-lg border border-line bg-canvas p-4 text-sm mb-5">
          <DetailRow label="Requested by">
            <span className="font-medium text-fg">{details.user_name}</span>
            <span className="ml-1.5 text-muted">
              ({details.role.replace(/_/g, " ")})
            </span>
          </DetailRow>
          <DetailRow label="Query">
            <span className="text-fg">{details.query}</span>
          </DetailRow>
          <DetailRow label="Compliance">
            <code className="block text-xs font-mono bg-surface rounded px-2 py-1 whitespace-pre-wrap break-words border border-line">
              {details.compliance_verdict}
            </code>
          </DetailRow>
          <DetailRow label="Approval ID">
            <span className="font-mono text-xs text-muted">{approvalId}</span>
          </DetailRow>
        </dl>

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <Button variant="danger" onClick={onReject} disabled={loading}>
            Reject
          </Button>
          <Button
            variant="primary"
            onClick={onApprove}
            loading={loading}
            className="bg-green-700 hover:bg-green-800 dark:bg-green-600 dark:hover:bg-green-500 text-white"
          >
            Approve &amp; Continue
          </Button>
        </div>
      </div>
    </div>
  );
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <dt className="w-28 shrink-0 font-medium text-muted">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  );
}
