import { RefreshCw, Activity, AlertCircle, CheckCircle2, Clock } from "lucide-react";
import { useMeshStatus } from "@/hooks/useMeshStatus";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { CenteredSpinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { Metric } from "@/components/ui/Metric";
import { cn } from "@/lib/utils";
import type { NodeHealth } from "@/types/mesh";

// Node descriptions matching the 4-node topology in SYSTEM_FLOW.md (AgentMesh 15.0.6.2026)
const NODE_INFO: Record<string, { label: string; description: string }> = {
  compliance:   { label: "Compliance",    description: "Semantic safety guardrail — LLM-based review; replies COMPLIANCE_PASSED / COMPLIANCE_FAILED" },
  data_agent:   { label: "Data Agent",    description: "Thin MCP client → DataLayer-as-a-Service (customer_360, pricing, margin, RWA tools)" },
  rag_agent:    { label: "RAG Agent",     description: "Thin MCP client → RAG-as-a-Service (search_documents — policy & regulatory knowledge)" },
  price_assist: { label: "Price Assist",  description: "Primary FAB banking coordinator — intent classification, delegates to Data & RAG agents" },
};

export default function MeshStatusPage() {
  const { data: nodes, isLoading, isError, refetch, isFetching } = useMeshStatus();

  const onlineCount = nodes?.filter((n) => n.status === "ok").length ?? 0;
  const totalCount = nodes?.length ?? 2;

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8 max-w-6xl mx-auto w-full">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-fg">Mesh Status</h1>
          <p className="text-sm text-muted mt-0.5">
            Real-time health of all A2A agent nodes — refreshes every 15 s
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} />
          Refresh
        </button>
      </div>

      {/* Overall stat */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Metric
          label="Nodes Online"
          value={isLoading ? "—" : `${onlineCount} / ${totalCount}`}
          tone={
            isLoading ? "default"
            : onlineCount === totalCount ? "good"
            : onlineCount === 0 ? "bad"
            : "warn"
          }
        />
        <Metric
          label="Status"
          value={
            isLoading ? "Checking…"
            : onlineCount === totalCount ? "All healthy"
            : onlineCount === 0 ? "All offline"
            : "Partial"
          }
          tone={
            isLoading ? "default"
            : onlineCount === totalCount ? "good"
            : onlineCount === 0 ? "bad"
            : "warn"
          }
        />
      </div>

      {/* Node cards */}
      {isLoading ? (
        <CenteredSpinner label="Checking node health…" />
      ) : isError ? (
        <div className="flex items-center gap-3 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-xl p-4">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <div>
            <p className="font-medium text-sm">Cannot reach API server</p>
            <p className="text-xs mt-0.5 text-red-500">
              Make sure the API server is running: <code>python api_server.py</code>
            </p>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {nodes?.map((node) => (
            <NodeCard key={node.name} node={node} />
          ))}
        </div>
      )}

      {/* How-to hint */}
      <div className="text-xs text-muted bg-canvas border border-line rounded-xl p-4 space-y-1">
        <p className="font-medium text-fg">Starting the mesh</p>
        <p>1. <code>python launch_mesh.py</code> — starts the A2A agent servers</p>
        <p>2. <code>python api_server.py</code> — starts the REST API bridge (port 8000)</p>
        <p>3. <code>npm run dev</code> — starts this UI (port 5173)</p>
      </div>
    </div>
  );
}

// ── NodeCard ─────────────────────────────────────────────────────────────────

function NodeCard({ node }: { node: NodeHealth }) {
  const info = NODE_INFO[node.name] ?? { label: node.name, description: "" };
  const isOk = node.status === "ok";
  const isError = node.status === "error";

  return (
    <Card>
      <CardHeader
        title={info.label}
        subtitle={`Port ${node.port} · ${node.url}`}
        icon={
          isOk ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-500" />
          ) : isError ? (
            <AlertCircle className="h-5 w-5 text-red-500" />
          ) : (
            <Activity className="h-5 w-5 text-muted" />
          )
        }
        action={
          <Badge
            tone={isOk ? "green" : isError ? "red" : "slate"}
          >
            {node.status}
          </Badge>
        }
      />
      <CardBody>
        <p className="text-xs text-muted mb-3">{info.description}</p>
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-canvas rounded-lg p-2">
            <p className="text-xs text-muted">Model</p>
            <p className="text-sm font-medium text-fg truncate">
              {node.model ?? "—"}
            </p>
          </div>
          <div className="bg-canvas rounded-lg p-2">
            <p className="text-xs text-muted flex items-center gap-1">
              <Clock className="h-3 w-3" /> Uptime
            </p>
            <p className="text-sm font-medium text-fg">
              {node.uptime_seconds != null
                ? formatUptime(node.uptime_seconds)
                : "—"}
            </p>
          </div>
        </div>
        {node.error && (
          <p className="mt-2 text-xs text-red-500 truncate" title={node.error}>
            {node.error}
          </p>
        )}
      </CardBody>
    </Card>
  );
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}
