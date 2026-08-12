import { agentColor } from '../App.jsx'
import './ReasoningPanel.css'

const FIELD_LABELS = {
  called_because: 'Called because',
  context:        'Context',
  action:         'Action taken',
  decision:       'Decision',
}

export default function ReasoningPanel({ items }) {
  if (!items.length) {
    return (
      <div className="reasoning-empty">
        Reasoning blocks will appear here as agents respond.
      </div>
    )
  }

  return (
    <div className="reasoning-list">
      {items.map((item, i) => {
        const color = agentColor(item.agent)
        const displayName = item.agent.replace('_agent', '')
        return (
          <div key={i} className="reasoning-card" style={{ borderLeftColor: color }}>
            <div className="reasoning-agent" style={{ color }}>
              <span className="reasoning-dot" style={{ background: color }} />
              {displayName}
              <span className="reasoning-seq">#{i + 1}</span>
            </div>

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

            {/* Render any extra keys the model added */}
            {Object.entries(item)
              .filter(([k]) => !['type', 'agent', ...Object.keys(FIELD_LABELS)].includes(k))
              .map(([k, v]) => (
                <div key={k} className="reasoning-field">
                  <div className="reasoning-field-label">{k.replace(/_/g, ' ')}</div>
                  <div className="reasoning-field-value">{String(v)}</div>
                </div>
              ))}
          </div>
        )
      })}
    </div>
  )
}
