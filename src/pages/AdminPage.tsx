import { useState } from "react";
import { Database, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useDeleteCollection, useReady, useReindex } from "@/hooks/useSystem";

type DialogKind = "reindex" | "delete" | null;

export function AdminPage() {
  const ready = useReady();
  const reindex = useReindex();
  const del = useDeleteCollection();
  const [dialog, setDialog] = useState<DialogKind>(null);

  const lastResult = reindex.data ?? del.data;
  const lastError = reindex.error ?? del.error;
  const busy = reindex.isPending || del.isPending;

  return (
    <div className="space-y-6">
      {/* Service status */}
      <Card>
        <CardHeader
          title="Service readiness"
          icon={<Database className="h-5 w-5" />}
          action={
            <Button variant="ghost" size="sm" onClick={() => ready.refetch()} loading={ready.isFetching}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
          }
        />
        <CardBody>
          {ready.isError ? (
            <Alert variant="warning" title="Backend not reachable">
              {ready.error.message}
            </Alert>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted">Overall:</span>
                <Badge tone={ready.data?.status === "ready" ? "green" : "amber"}>
                  {ready.data?.status ?? "—"}
                </Badge>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted">Vector DB:</span>
                <Badge tone={ready.data?.vectordb ? "green" : "red"}>
                  {ready.data?.vectordb ? "reachable" : "unreachable"}
                </Badge>
              </div>
            </div>
          )}
        </CardBody>
      </Card>

      {/* Result / error banners */}
      {lastResult && (
        <Alert variant="success" title="Operation complete">
          {lastResult.status} · collection <code>{lastResult.collection}</code>
        </Alert>
      )}
      {lastError && (
        <Alert variant="error" title="Operation failed">
          {lastError.message}
        </Alert>
      )}

      {/* Destructive operations */}
      <Card>
        <CardHeader title="Collection management" subtitle="These operations affect indexed data. Proceed with care." />
        <CardBody className="space-y-4">
          <div className="flex flex-col gap-3 rounded-lg border border-line p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium text-fg">Reindex collection</p>
              <p className="text-xs text-muted">
                Drops and recreates the collection. All indexed chunks are removed; re-ingest to repopulate.
              </p>
            </div>
            <Button variant="outline" onClick={() => setDialog("reindex")} disabled={busy}>
              <RotateCcw className="h-4 w-4" /> Reindex
            </Button>
          </div>

          <div className="flex flex-col gap-3 rounded-lg border border-red-200 bg-red-50/40 p-4 dark:border-red-500/30 dark:bg-red-500/10 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium text-red-800 dark:text-red-300">Delete collection</p>
              <p className="text-xs text-red-600/80 dark:text-red-400/80">
                Permanently deletes the configured collection and all its vectors.
              </p>
            </div>
            <Button variant="danger" onClick={() => setDialog("delete")} disabled={busy}>
              <Trash2 className="h-4 w-4" /> Delete
            </Button>
          </div>
        </CardBody>
      </Card>

      <ConfirmDialog
        open={dialog === "reindex"}
        title="Reindex collection?"
        description="This drops and recreates the collection. All currently indexed chunks will be removed."
        confirmLabel="Reindex"
        loading={reindex.isPending}
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          reindex.mutate(undefined, { onSettled: () => setDialog(null) });
        }}
      />

      <ConfirmDialog
        open={dialog === "delete"}
        title="Delete collection?"
        description="This permanently deletes the configured collection and every vector in it. This cannot be undone."
        confirmLabel="Delete collection"
        loading={del.isPending}
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          del.mutate(undefined, { onSettled: () => setDialog(null) });
        }}
      />
    </div>
  );
}
