import { agentColor } from '../App.jsx'
import './AgentFlowPanel.css'

export default function AgentFlowPanel({ flow, toolCalls }) {
  const allAgents = Array.from(
    new Set([
      ...flow.map(f => f.from),
      ...flow.map(f => f.to),
    ])
  )

  return (
    <div className="flow-panel">
      {/* Agent nodes */}
      <div className="flow-section-label">Agent Mesh</div>
      <div className="flow-nodes">
        {['triage_agent', 'account_agent', 'card_agent', 'loan_agent', 'transfer_agent', 'fraud_agent'].map(name => {
          const active = allAgents.includes(name)
          const color = agentColor(name)
          const label = name.replace('_agent', '')
          return (
            <div key={name} className={`flow-node ${active ? 'flow-node-active' : ''}`}
              style={active ? { borderColor: color, color } : {}}>
              {label}
            </div>
          )
        })}
      </div>

      {/* Handoff path */}
      {flow.length > 0 && (
        <>
          <div className="flow-section-label" style={{ marginTop: 20 }}>Handoff Path</div>
          <div className="flow-path">
            {flow.map((h, i) => (
              <span key={i} className="flow-path-item">
                <span className="flow-path-agent" style={{ color: agentColor(h.from) }}>
                  {h.from.replace('_agent', '')}
                </span>
                <span className="flow-path-arrow">→</span>
                <span className="flow-path-agent" style={{ color: agentColor(h.to) }}>
                  {h.to.replace('_agent', '')}
                </span>
                {i < flow.length - 1 && <span className="flow-path-sep">·</span>}
              </span>
            ))}
          </div>
        </>
      )}

      {/* Tool calls */}
      {toolCalls.length > 0 && (
        <>
          <div className="flow-section-label" style={{ marginTop: 20 }}>Tool Calls</div>
          <div className="flow-tools">
            {toolCalls.map((tc, i) => {
              const color = agentColor(tc.agent)
              return (
                <div key={i} className="flow-tool-row">
                  <div className="flow-tool-agent-dot" style={{ background: color }} />
                  <div>
                    <div className="flow-tool-name">{tc.tool}</div>
                    <div className="flow-tool-agent">{tc.agent.replace('_agent', '')}</div>
                  </div>
                  <div className="flow-tool-args">
                    {Object.entries(tc.args || {}).map(([k, v]) => (
                      <div key={k} className="flow-tool-arg">
                        <span className="flow-tool-arg-key">{k}:</span> {String(v)}
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}

      {flow.length === 0 && toolCalls.length === 0 && (
        <div className="flow-empty">Agent flow and tool calls will appear here.</div>
      )}
    </div>
  )
}
