import './HitlGate.css'

export default function HitlGate({ gate, onApprove, onReject }) {
  const isActive = onApprove !== null
  return (
    <div className={`hitl-gate ${isActive ? 'active' : 'inactive'}`}>
      <div className="gate-header">
        <span className="gate-badge">HITL</span>
        <span className="gate-label">{gate.label || gate.tool}</span>
        <span className="gate-role">Requires: {gate.role}</span>
      </div>

      <div className="gate-tool">Tool: <code>{gate.tool}</code></div>

      {gate.args && Object.keys(gate.args).length > 0 && (
        <div className="gate-args">
          {Object.entries(gate.args).map(([k, v]) => (
            <div key={k} className="gate-arg-row">
              <span className="gate-arg-key">{k}</span>
              <span className="gate-arg-val">{String(v)}</span>
            </div>
          ))}
        </div>
      )}

      {isActive && (
        <div className="gate-actions">
          <button className="btn-approve" onClick={onApprove}>✓ Approve</button>
          <button className="btn-reject"  onClick={onReject}>✗ Reject</button>
        </div>
      )}

      {!isActive && (
        <div className="gate-decided">Decision recorded</div>
      )}
    </div>
  )
}
