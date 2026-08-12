import { useState, useEffect, useRef, useCallback } from 'react'
import ChatPanel from './components/ChatPanel.jsx'
import ReasoningPanel from './components/ReasoningPanel.jsx'
import AgentFlowPanel from './components/AgentFlowPanel.jsx'
import ApprovalsPanel from './components/ApprovalsPanel.jsx'

export const AGENT_COLORS = {
  triage_agent:   '#4f86c6',
  account_agent:  '#5aaa65',
  card_agent:     '#e09a3a',
  loan_agent:     '#9b6dd6',
  transfer_agent: '#e06353',
  fraud_agent:    '#c9404e',
}

export function agentColor(name) {
  return AGENT_COLORS[name] || '#8b90a8'
}

export default function App() {
  const [sessionId, setSessionId]       = useState(null)
  const [starting, setStarting]         = useState(false)
  const [messages, setMessages]         = useState([])
  const [reasoning, setReasoning]       = useState([])
  const [agentFlow, setAgentFlow]       = useState([])
  const [toolCalls, setToolCalls]       = useState([])
  const [approvals, setApprovals]       = useState([])   // all approval requests
  const [pendingGateId, setPendingGateId] = useState(null) // request_id of active gate
  const [activeAgent, setActiveAgent]   = useState(null)
  const [needsInput, setNeedsInput]     = useState(false)
  const [done, setDone]                 = useState(false)
  const [tab, setTab]                   = useState('reasoning')
  const [approvalBadge, setApprovalBadge] = useState(0) // unseen approval count

  const esRef = useRef(null)
  // Track the current gate ref so sendApproval can read it after state closure
  const pendingGateRef = useRef(null)

  const appendToken = useCallback((agent, text) => {
    setMessages(prev => {
      const last = prev[prev.length - 1]
      if (last && last.role === 'agent' && last.agent === agent && last.streaming) {
        return [...prev.slice(0, -1), { ...last, text: last.text + text }]
      }
      return [...prev, { role: 'agent', agent, text, streaming: true }]
    })
  }, [])

  const finalizeLastMessage = useCallback(() => {
    setMessages(prev => {
      if (!prev.length) return prev
      const last = prev[prev.length - 1]
      if (last.streaming) return [...prev.slice(0, -1), { ...last, streaming: false }]
      return prev
    })
  }, [])

  function connectSSE(sid) {
    if (esRef.current) esRef.current.close()
    const es = new EventSource(`/api/stream/${sid}`)
    esRef.current = es

    es.onmessage = (e) => {
      const ev = JSON.parse(e.data)

      if (ev.type === 'agent_active') {
        setActiveAgent(ev.agent)
      }

      else if (ev.type === 'handoff') {
        finalizeLastMessage()
        setAgentFlow(f => [...f, ev])
      }

      else if (ev.type === 'reasoning') {
        setReasoning(r => [...r, ev])
        setTab(t => t === 'approvals' ? t : 'reasoning')
      }

      else if (ev.type === 'token') {
        appendToken(ev.agent, ev.text)
      }

      else if (ev.type === 'tool_call') {
        setToolCalls(t => [...t, ev])
      }

      else if (ev.type === 'needs_input') {
        finalizeLastMessage()
        setNeedsInput(true)
        setPendingGateId(null)
        pendingGateRef.current = null
      }

      else if (ev.type === 'needs_approval') {
        finalizeLastMessage()
        // Add to approvals list (with decided=undefined = pending)
        const gateEntry = { ...ev, decided: undefined }
        pendingGateRef.current = gateEntry
        setApprovals(a => [...a, gateEntry])
        setPendingGateId(ev.request_id)
        setNeedsInput(false)
        // Switch to Approvals tab and bump badge
        setTab('approvals')
        setApprovalBadge(b => b + 1)
        // Add a compact notice to chat
        setMessages(m => [...m, { role: 'gate_notice', label: ev.label, gate: ev.gate, role_req: ev.role }])
      }

      else if (ev.type === 'session_done') {
        finalizeLastMessage()
        setDone(true)
        setNeedsInput(false)
        setPendingGateId(null)
        es.close()
      }

      else if (ev.type === 'error') {
        finalizeLastMessage()
        setMessages(m => [...m, { role: 'error', text: ev.message }])
        es.close()
      }
    }

    es.onerror = () => { /* SSE closes normally after session_done */ }
  }

  async function startSession() {
    setStarting(true)
    try {
      const res = await fetch('/api/session', { method: 'POST' })
      const { session_id } = await res.json()
      setSessionId(session_id)
      connectSSE(session_id)
      setNeedsInput(true)
    } catch (err) {
      console.error('Failed to start session', err)
    } finally {
      setStarting(false)
    }
  }

  async function sendMessage(text) {
    if (!text.trim() || !sessionId) return
    setMessages(m => [...m, { role: 'user', text }])
    setNeedsInput(false)
    await fetch(`/api/message/${sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
  }

  async function sendApproval(requestId, approved) {
    // Mark the gate as decided in approvals list
    setApprovals(a => a.map(apv =>
      apv.request_id === requestId ? { ...apv, decided: approved } : apv
    ))
    setPendingGateId(null)
    pendingGateRef.current = null
    setApprovalBadge(0)

    await fetch(`/api/approve/${sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: requestId, approved }),
    })
  }

  // Clear badge when user views the approvals tab
  function handleTabChange(t) {
    setTab(t)
    if (t === 'approvals') setApprovalBadge(0)
  }

  useEffect(() => {
    return () => { if (esRef.current) esRef.current.close() }
  }, [])

  const pendingGate = approvals.find(a => a.request_id === pendingGateId) || null

  return (
    <>
      <header className="header">
        <div className="header-logo">RB</div>
        <div>
          <div className="header-title">Retail Banking — Handoff POC</div>
          <div className="header-subtitle">Microsoft Agent Framework · HandoffBuilder · 6-agent mesh</div>
        </div>
        <div className="header-status">
          <div className={`status-dot ${sessionId && !done ? 'active' : ''}`} />
          {sessionId ? (done ? 'Session complete' : `Active · ${activeAgent || 'waiting'}`) : 'No session'}
        </div>
      </header>

      <div className="layout">
        {!sessionId ? (
          <div className="start-screen">
            <h2>Retail Banking Support</h2>
            <p>
              This POC demonstrates a 6-agent handoff mesh where each agent's
              LLM reasoning is captured and displayed in real time.
            </p>
            <button className="btn-start" onClick={startSession} disabled={starting}>
              {starting ? 'Starting...' : 'Start New Session'}
            </button>
          </div>
        ) : (
          <ChatPanel
            messages={messages}
            needsInput={needsInput}
            pendingGate={pendingGate}
            done={done}
            onSend={sendMessage}
            activeAgent={activeAgent}
          />
        )}

        <div className="right-panel">
          <div className="panel-tabs">
            <button className={`panel-tab ${tab === 'reasoning' ? 'active' : ''}`} onClick={() => handleTabChange('reasoning')}>
              Reasoning ({reasoning.length})
            </button>
            <button className={`panel-tab ${tab === 'approvals' ? 'active' : ''}`} onClick={() => handleTabChange('approvals')}>
              Approvals{approvalBadge > 0 ? ` ●` : ` (${approvals.length})`}
            </button>
            <button className={`panel-tab ${tab === 'flow' ? 'active' : ''}`} onClick={() => handleTabChange('flow')}>
              Flow
            </button>
          </div>
          <div className="panel-content">
            {tab === 'reasoning' && <ReasoningPanel items={reasoning} />}
            {tab === 'approvals' && (
              <ApprovalsPanel
                approvals={approvals}
                pendingId={pendingGateId}
                onApprove={(id) => sendApproval(id, true)}
                onReject={(id) => sendApproval(id, false)}
              />
            )}
            {tab === 'flow' && <AgentFlowPanel flow={agentFlow} toolCalls={toolCalls} />}
          </div>
        </div>
      </div>
    </>
  )
}
