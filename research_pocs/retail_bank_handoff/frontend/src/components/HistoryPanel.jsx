import { useState } from 'react'
import { agentColor } from '../App.jsx'
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
        return null
      })}
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
                <MessageReplay messages={session.messages || []} />
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
