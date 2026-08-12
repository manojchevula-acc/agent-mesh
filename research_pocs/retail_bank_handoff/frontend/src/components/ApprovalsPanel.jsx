import HitlGate from './HitlGate.jsx'
import './ApprovalsPanel.css'

export default function ApprovalsPanel({ approvals, pendingId, onApprove, onReject }) {
  if (!approvals.length) {
    return (
      <div className="approvals-empty">
        HITL approval requests will appear here when agents need human authorisation.
      </div>
    )
  }

  return (
    <div className="approvals-list">
      {[...approvals].reverse().map((apv, i) => {
        const isPending = apv.request_id === pendingId
        const isDecided = apv.decided !== undefined
        return (
          <div key={i} className="approval-item">
            <HitlGate
              gate={apv}
              onApprove={isPending && !isDecided ? () => onApprove(apv.request_id) : null}
              onReject={isPending && !isDecided ? () => onReject(apv.request_id) : null}
            />
            {isDecided && (
              <div className={`apv-verdict ${apv.decided ? 'approved' : 'rejected'}`}>
                {apv.decided ? '✓ Approved' : '✗ Rejected'}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
