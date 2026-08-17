import { useState } from 'react'
import { agentColor } from '../constants/agentColors.js'
import AgentFlowPanel from './AgentFlowPanel.jsx'
import ApprovalsPanel from './ApprovalsPanel.jsx'
import ReasoningPanel from './ReasoningPanel.jsx'
import './HistoryPanel.css'

function formatDateTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function agentPath(agentFlow) {
  if (!agentFlow || agentFlow.length === 0) return '—'
  const nodes = [agentFlow[0].from, ...agentFlow.map(h => h.to)]
  const unique = nodes.filter((n, i) => i === 0 || n !== nodes[i - 1])
  return unique.map(n => n.replace('_agent', '')).join(' → ')
}

function MessageReplay({ messages }) {
  return (
    <div className="history-replay">
      {messages.map((msg, i) => {
        if (msg.role === 'user') {
          return (
            <div key={i} className="hr-row hr-user">
              <div className="hr-bubble hr-bubble-user">{msg.text}</div>
            </div>
          )
        }
        if (msg.role === 'agent') {
          const color = agentColor(msg.agent)
          return (
            <div key={i} className="hr-row hr-agent">
              <div className="hr-agent-tag" style={{ background: color }}>
                {msg.agent.replace('_agent', '')}
              </div>
              <div className="hr-bubble hr-bubble-agent" style={{ borderLeftColor: color }}>
                {msg.text}
              </div>
            </div>
          )
        }
        if (msg.role === 'handoff') {
          return (
            <div key={i} className="hr-handoff">
              <span style={{ color: agentColor(msg.from) }}>{msg.from.replace('_agent', '')}</span>
              <span className="hr-arrow">→</span>
              <span style={{ color: agentColor(msg.to) }}>{msg.to.replace('_agent', '')}</span>
            </div>
          )
        }
        if (msg.role === 'multi_intent') {
          return (
            <div key={i} className="hr-event hr-multi-intent">
              <strong>Multi-Intent Queue</strong>
              {msg.pending?.length
                ? msg.pending.map(item => <span key={item} className="hr-chip">{item}</span>)
                : <span>All intents handled</span>}
            </div>
          )
        }
        if (msg.role === 'gate_notice') {
          return (
            <div key={i} className="hr-event hr-gate-notice">
              <strong>{msg.label}</strong>
              <span>Requires {msg.role_req}</span>
            </div>
          )
        }
        if (msg.role === 'error') {
          return <div key={i} className="hr-event hr-error">Error: {msg.text}</div>
        }
        return null
      })}
    </div>
  )
}

function SessionSnapshot({ session }) {
  const [snapshotTab, setSnapshotTab] = useState('chat')
  const tabs = [
    ['chat', 'Chat'],
    ['reasoning', `Reasoning (${(session.reasoning || []).length})`],
    ['flow', 'Flow'],
    ['approvals', `Approvals (${(session.approvals || []).length})`],
  ]

  return (
    <div className="history-snapshot">
      <div className="history-snapshot-tabs" role="tablist" aria-label="Saved session views">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            className={`history-snapshot-tab ${snapshotTab === id ? 'active' : ''}`}
            onClick={() => setSnapshotTab(id)}
            type="button"
            role="tab"
            aria-selected={snapshotTab === id}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="history-snapshot-content">
        {snapshotTab === 'chat' && <MessageReplay messages={session.messages || []} />}
        {snapshotTab === 'reasoning' && (
          <ReasoningPanel
            items={session.reasoning || []}
            handoffCount={(session.agentFlow || []).length}
            toolCallCount={(session.toolCalls || []).length}
          />
        )}
        {snapshotTab === 'flow' && (
          <AgentFlowPanel flow={session.agentFlow || []} toolCalls={session.toolCalls || []} />
        )}
        {snapshotTab === 'approvals' && (
          <ApprovalsPanel
            approvals={session.approvals || []}
            pendingId={null}
            onApprove={() => {}}
            onReject={() => {}}
          />
        )}
      </div>
    </div>
  )
}

export default function HistoryPanel({ history, onClear }) {
  const [expanded, setExpanded] = useState(null)

  if (history.length === 0) {
    return (
      <div className="history-empty">
        Completed sessions will be saved here automatically.
      </div>
    )
  }

  return (
    <div className="history-panel">
      <div className="history-list">
        {[...history].reverse().map((session, ri) => {
          const i = history.length - 1 - ri
          const isOpen = expanded === i
          const msgCount = (session.messages || []).filter(m => m.role === 'user' || m.role === 'agent').length
          const path = agentPath(session.agentFlow || [])
          return (
            <div key={session.id} className={`history-item ${isOpen ? 'history-item-open' : ''}`}>
              <div className="history-item-header" onClick={() => setExpanded(isOpen ? null : i)}>
                <div className="history-item-meta">
                  <span className="history-item-date">{formatDateTime(session.timestamp)}</span>
                  <span className="history-item-badge">{msgCount} msgs</span>
                </div>
                <div className="history-item-path">{path}</div>
                <div className="history-item-stats">
                  <span>{(session.reasoning || []).length} steps</span>
                  <span>·</span>
                  <span>{(session.agentFlow || []).length} handoffs</span>
                  <span>·</span>
                  <span>{(session.toolCalls || []).length} tools</span>
                  <span className="history-chevron">{isOpen ? '▲' : '▼'}</span>
                </div>
              </div>

              {isOpen && (
                <SessionSnapshot session={session} />
              )}
            </div>
          )
        })}
      </div>

      <div className="history-footer">
        <button className="btn-clear-history" onClick={onClear}>
          Clear history
        </button>
      </div>
    </div>
  )
}
