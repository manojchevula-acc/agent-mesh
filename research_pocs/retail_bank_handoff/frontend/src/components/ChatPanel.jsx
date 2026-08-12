import { useState, useRef, useEffect } from 'react'
import { agentColor } from '../App.jsx'
import './ChatPanel.css'

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

export default function ChatPanel({ messages, needsInput, pendingGate, done, onSend, activeAgent, onNewSession }) {
  const [input, setInput] = useState('')
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handleSend() {
    const text = input.trim()
    if (!text) return
    setInput('')
    onSend(text)
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">Session started. Type your first message below.</div>
        )}

        {messages.map((msg, i) => {
          if (msg.role === 'user') {
            return (
              <div key={i} className="msg-row msg-user">
                <div className="msg-bubble msg-bubble-user">{msg.text}</div>
                <div className="msg-meta">
                  <span className="msg-author">You</span>
                  {msg.timestamp && <span className="msg-ts">{formatTime(msg.timestamp)}</span>}
                </div>
              </div>
            )
          }

          if (msg.role === 'agent') {
            const color = agentColor(msg.agent)
            return (
              <div key={i} className="msg-row msg-agent">
                <div className="msg-agent-tag" style={{ background: color }}>
                  {msg.agent.replace('_agent', '')}
                </div>
                <div className="msg-bubble msg-bubble-agent" style={{ borderLeftColor: color }}>
                  {msg.text}
                  {msg.streaming && <span className="cursor-blink" />}
                </div>
                {msg.timestamp && (
                  <div className="msg-meta">
                    <span className="msg-ts">{formatTime(msg.timestamp)}</span>
                  </div>
                )}
              </div>
            )
          }

          if (msg.role === 'handoff') {
            const fromColor = agentColor(msg.from)
            const toColor   = agentColor(msg.to)
            return (
              <div key={i} className="handoff-divider">
                <span className="handoff-from" style={{ color: fromColor }}>
                  {msg.from.replace('_agent', '')}
                </span>
                <span className="handoff-arrow">→</span>
                <span className="handoff-to" style={{ color: toColor }}>
                  {msg.to.replace('_agent', '')}
                </span>
                {msg.timestamp && (
                  <span className="handoff-ts">{formatTime(msg.timestamp)}</span>
                )}
              </div>
            )
          }

          if (msg.role === 'gate_notice') {
            return (
              <div key={i} className="gate-notice">
                <span className="gate-notice-icon">🔒</span>
                <span>
                  <strong>{msg.label}</strong> — requires {msg.role_req}
                </span>
                <span className="gate-notice-hint">→ Approvals tab</span>
              </div>
            )
          }

          if (msg.role === 'error') {
            return (
              <div key={i} className="msg-error">
                Error: {msg.text}
              </div>
            )
          }

          return null
        })}

        {done && (
          <div className="session-done">
            <div className="session-done-text">Session complete.</div>
            <button className="btn-new-session" onClick={onNewSession}>
              Start New Session
            </button>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {!done && (
        <div className="chat-input-bar">
          {needsInput && !pendingGate && (
            <>
              <textarea
                className="chat-input"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKey}
                placeholder="Type your message..."
                rows={2}
                autoFocus
              />
              <button className="btn-send" onClick={handleSend} disabled={!input.trim()}>
                Send
              </button>
            </>
          )}
          {pendingGate && (
            <div className="waiting-note waiting-approval">
              Approval required — see the <strong>Approvals</strong> tab →
            </div>
          )}
          {!needsInput && !pendingGate && (
            <div className="waiting-note">
              {activeAgent ? `${activeAgent} is thinking...` : 'Processing...'}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
