import { useState } from 'react'
import { agentColor } from '../App.jsx'
import './ReasoningPanel.css'

const FIELD_LABELS = {
  called_because: 'Called because',
  context:        'Context',
  action:         'Action taken',
  decision:       'Decision',
}

export default function ReasoningPanel({ items, handoffCount = 0, toolCallCount = 0 }) {
  const [collapsed, setCollapsed] = useState({})

  function toggle(i) {
    setCollapsed(prev => ({ ...prev, [i]: !prev[i] }))
  }

  if (!items.length) {
    return (
      <div className="reasoning-empty">
        Reasoning blocks will appear here as agents respond.
      </div>
    )
  }

  return (
    <div className="reasoning-wrapper">
      <div className="reasoning-summary">
        <span>{items.length} step{items.length !== 1 ? 's' : ''}</span>
        <span className="reasoning-summary-sep">·</span>
        <span>{handoffCount} handoff{handoffCount !== 1 ? 's' : ''}</span>
        <span className="reasoning-summary-sep">·</span>
        <span>{toolCallCount} tool call{toolCallCount !== 1 ? 's' : ''}</span>
      </div>

      <div className="reasoning-list">
        {items.map((item, i) => {
          const color = agentColor(item.agent)
          const displayName = item.agent.replace('_agent', '')
          const isCollapsed = collapsed[i]
          return (
            <div key={i} className="reasoning-card" style={{ borderLeftColor: color }}>
              <div
                className="reasoning-agent reasoning-agent-clickable"
                style={{ color }}
                onClick={() => toggle(i)}
              >
                <span className="reasoning-dot" style={{ background: color }} />
                {displayName}
                <span className="reasoning-seq">#{i + 1}</span>
                <span className="reasoning-chevron">{isCollapsed ? '▶' : '▼'}</span>
              </div>

              {!isCollapsed && (
                <>
                  {Object.entries(FIELD_LABELS).map(([key, label]) => {
                    const val = item[key]
                    if (!val) return null
                    return (
                      <div key={key} className="reasoning-field">
                        <div className="reasoning-field-label">{label}</div>
                        <div className="reasoning-field-value">{val}</div>
                      </div>
                    )
                  })}

                  {Object.entries(item)
                    .filter(([k]) => !['type', 'agent', ...Object.keys(FIELD_LABELS)].includes(k))
                    .map(([k, v]) => (
                      <div key={k} className="reasoning-field">
                        <div className="reasoning-field-label">{k.replace(/_/g, ' ')}</div>
                        <div className="reasoning-field-value">{String(v)}</div>
                      </div>
                    ))}
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
