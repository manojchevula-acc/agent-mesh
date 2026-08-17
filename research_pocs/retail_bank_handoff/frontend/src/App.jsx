import { useState, useEffect, useRef, useCallback } from 'react'
import ChatPanel from './components/ChatPanel.jsx'
import ReasoningPanel from './components/ReasoningPanel.jsx'
import AgentFlowPanel from './components/AgentFlowPanel.jsx'
import ApprovalsPanel from './components/ApprovalsPanel.jsx'
import HistoryPanel from './components/HistoryPanel.jsx'

function loadHistory() {
  try {
    const ids = JSON.parse(localStorage.getItem('rbh_session_ids') || '[]')
    return ids
      .map(id => {
        try { return JSON.parse(localStorage.getItem(`rbh_session_${id}`)) }
        catch { return null }
      })
      .filter(Boolean)
  } catch {
    return []
  }
}

function mergeHistory(...collections) {
  const records = new Map()
  collections.flat().forEach(record => {
    if (!record?.id) return
    const existing = records.get(record.id)
    if (!existing || Number(record.timestamp) > Number(existing.timestamp)) {
      records.set(record.id, record)
    }
  })
  return [...records.values()].sort((a, b) => Number(a.timestamp) - Number(b.timestamp))
}

function persistLocalHistory(records) {
  try {
    const previousIds = JSON.parse(localStorage.getItem('rbh_session_ids') || '[]')
    const nextIds = records.map(record => record.id)
    previousIds.filter(id => !nextIds.includes(id)).forEach(id => localStorage.removeItem(`rbh_session_${id}`))
    records.forEach(record => localStorage.setItem(`rbh_session_${record.id}`, JSON.stringify(record)))
    localStorage.setItem('rbh_session_ids', JSON.stringify(nextIds))
  } catch { /* storage quota or private mode */ }
}

async function persistServerHistory(record) {
  try {
    await fetch(`/api/session-history/${record.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(record),
    })
  } catch { /* local history remains available offline */ }
}

export default function App() {
  const [sessionId, setSessionId]           = useState(null)
  const [starting, setStarting]             = useState(false)
  const [messages, setMessages]             = useState([])
  const [reasoning, setReasoning]           = useState([])
  const [agentFlow, setAgentFlow]           = useState([])
  const [toolCalls, setToolCalls]           = useState([])
  const [approvals, setApprovals]           = useState([])
  const [pendingGateId, setPendingGateId]   = useState(null)
  const [activeAgent, setActiveAgent]       = useState(null)
  const [needsInput, setNeedsInput]         = useState(false)
  const [done, setDone]                     = useState(false)
  const [tab, setTab]                       = useState('reasoning')
  const [approvalBadge, setApprovalBadge]   = useState(0)
  const [multiIntentQueue, setMultiIntentQueue] = useState([])
  const [history, setHistory]               = useState(() => loadHistory())

  const esRef          = useRef(null)
  const pendingGateRef = useRef(null)
  // Refs mirror live state so the session_done save sees fresh values
  const messagesRef  = useRef([])
  const reasoningRef = useRef([])
  const agentFlowRef = useRef([])
  const toolCallsRef = useRef([])
  const approvalsRef = useRef([])

  const appendToken = useCallback((agent, text) => {
    setMessages(prev => {
      const last = prev[prev.length - 1]
      let next
      if (last && last.role === 'agent' && last.agent === agent && last.streaming) {
        next = [...prev.slice(0, -1), { ...last, text: last.text + text }]
      } else {
        next = [...prev, { role: 'agent', agent, text, streaming: true, timestamp: Date.now() }]
      }
      messagesRef.current = next
      return next
    })
  }, [])

  const finalizeLastMessage = useCallback(() => {
    setMessages(prev => {
      if (!prev.length) return prev
      const last = prev[prev.length - 1]
      const next = last.streaming
        ? [...prev.slice(0, -1), { ...last, streaming: false }]
        : prev
      messagesRef.current = next
      return next
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
        setAgentFlow(f => {
          const next = [...f, ev]
          agentFlowRef.current = next
          return next
        })
        // Inject inline handoff divider into chat
        setMessages(prev => {
          const next = [...prev, { role: 'handoff', from: ev.from, to: ev.to, timestamp: Date.now() }]
          messagesRef.current = next
          return next
        })
      }

      else if (ev.type === 'reasoning') {
        setReasoning(r => {
          const next = [...r, ev]
          reasoningRef.current = next
          return next
        })
        setTab(t => t === 'approvals' ? t : 'reasoning')
      }

      else if (ev.type === 'multi_intent_queue') {
        setMultiIntentQueue(ev.pending)
        setMessages(prev => {
          const next = [...prev, { role: 'multi_intent', agent: ev.agent, pending: ev.pending, timestamp: Date.now() }]
          messagesRef.current = next
          return next
        })
      }

      else if (ev.type === 'token') {
        appendToken(ev.agent, ev.text)
      }

      else if (ev.type === 'tool_call') {
        setToolCalls(t => {
          const next = [...t, ev]
          toolCallsRef.current = next
          return next
        })
      }

      else if (ev.type === 'needs_input') {
        finalizeLastMessage()
        setNeedsInput(true)
        setPendingGateId(null)
        pendingGateRef.current = null
      }

      else if (ev.type === 'needs_approval') {
        finalizeLastMessage()
        const gateEntry = { ...ev, decided: undefined }
        pendingGateRef.current = gateEntry
        setApprovals(a => {
          const next = [...a, gateEntry]
          approvalsRef.current = next
          return next
        })
        setPendingGateId(ev.request_id)
        setNeedsInput(false)
        setTab('approvals')
        setApprovalBadge(b => b + 1)
        setMessages(m => {
          const next = [...m, { role: 'gate_notice', label: ev.label, gate: ev.gate, role_req: ev.role }]
          messagesRef.current = next
          return next
        })
      }

      else if (ev.type === 'session_done') {
        finalizeLastMessage()
        setDone(true)
        setNeedsInput(false)
        setPendingGateId(null)
        es.close()

        // Save completed session to localStorage using ref values (always fresh)
        const record = {
          id: sid,
          timestamp: Date.now(),
          messages:  messagesRef.current,
          reasoning: reasoningRef.current,
          agentFlow: agentFlowRef.current,
          toolCalls: toolCallsRef.current,
          approvals: approvalsRef.current,
        }
        setHistory(prev => {
          const next = mergeHistory(prev, [record])
          persistLocalHistory(next)
          return next
        })
        persistServerHistory(record)
      }

      else if (ev.type === 'error') {
        finalizeLastMessage()
        setMessages(m => {
          const next = [...m, { role: 'error', text: ev.message }]
          messagesRef.current = next
          return next
        })
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

  async function resetSession() {
    // Clean up current session on the backend
    if (sessionId) {
      try { await fetch(`/api/session/${sessionId}`, { method: 'DELETE' }) } catch { /* ignore */ }
    }
    if (esRef.current) { esRef.current.close(); esRef.current = null }

    // Reset all state
    setMessages([]);  messagesRef.current  = []
    setReasoning([]); reasoningRef.current = []
    setAgentFlow([]); agentFlowRef.current = []
    setToolCalls([]); toolCallsRef.current = []
    setApprovals([]); approvalsRef.current = []
    setPendingGateId(null)
    pendingGateRef.current = null
    setActiveAgent(null)
    setNeedsInput(false)
    setDone(false)
    setApprovalBadge(0)
    setMultiIntentQueue([])
    setTab('reasoning')
    setSessionId(null)

    // Start fresh
    await startSession()
  }

  async function sendMessage(text) {
    if (!text.trim() || !sessionId) return
    setMessages(m => {
      const next = [...m, { role: 'user', text, timestamp: Date.now() }]
      messagesRef.current = next
      return next
    })
    setNeedsInput(false)
    await fetch(`/api/message/${sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
  }

  async function sendApproval(requestId, approved) {
    setApprovals(a => {
      const next = a.map(apv => apv.request_id === requestId ? { ...apv, decided: approved } : apv)
      approvalsRef.current = next
      return next
    })
    setPendingGateId(null)
    pendingGateRef.current = null
    setApprovalBadge(0)

    await fetch(`/api/approve/${sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: requestId, approved }),
    })
  }

  async function clearHistory() {
    persistLocalHistory([])
    setHistory([])
    try { await fetch('/api/session-history', { method: 'DELETE' }) } catch { /* local clear still succeeds */ }
  }

  function handleTabChange(t) {
    setTab(t)
    if (t === 'approvals') setApprovalBadge(0)
  }

  useEffect(() => {
    let mounted = true
    const localRecords = loadHistory()

    async function synchronizeHistory() {
      try {
        const response = await fetch('/api/session-history')
        if (!response.ok) return
        const { records: serverRecords = [] } = await response.json()
        const merged = mergeHistory(localRecords, serverRecords)
        persistLocalHistory(merged)
        if (mounted) setHistory(current => mergeHistory(current, merged))

        const serverById = new Map(serverRecords.map(record => [record.id, record]))
        const recordsToUpload = localRecords.filter(record => {
          const serverRecord = serverById.get(record.id)
          return !serverRecord || Number(record.timestamp) > Number(serverRecord.timestamp)
        })
        await Promise.all(recordsToUpload.map(persistServerHistory))
      } catch { /* retain local history when server is unavailable */ }
    }

    synchronizeHistory()
    return () => {
      mounted = false
      if (esRef.current) esRef.current.close()
    }
  }, [])

  const pendingGate = approvals.find(a => a.request_id === pendingGateId) || null

  const historyBadge = history.length > 0 ? ` (${history.length})` : ''

  return (
    <>
      <header className="header">
        <div className="header-logo">RB</div>
        <div>
          <div className="header-title">Retail Banking — Handoff POC</div>
          <div className="header-subtitle">Microsoft Agent Framework · HandoffBuilder · 6-agent mesh</div>
        </div>
        <div className="header-right">
          <div className="header-status">
            <div className={`status-dot ${sessionId && !done ? 'active' : ''}`} />
            {sessionId ? (done ? 'Session complete' : `Active · ${activeAgent || 'waiting'}`) : 'No session'}
          </div>
          <button
            className="btn-devui-toggle"
            onClick={() => window.open('http://127.0.0.1:8080', '_blank')}
            title="Open MAF DevUI — start with: python devui_app.py"
          >
            ⇄ DevUI
          </button>
          {sessionId && (
            <button className="btn-new-session-header" onClick={resetSession}>
              + New Session
            </button>
          )}
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
            onNewSession={resetSession}
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
            <button className={`panel-tab ${tab === 'history' ? 'active' : ''}`} onClick={() => handleTabChange('history')}>
              History{historyBadge}
            </button>
          </div>
          <div className="panel-content">
            {tab === 'reasoning' && (
              <ReasoningPanel
                items={reasoning}
                handoffCount={agentFlow.length}
                toolCallCount={toolCalls.length}
              />
            )}
            {tab === 'approvals' && (
              <ApprovalsPanel
                approvals={approvals}
                pendingId={pendingGateId}
                onApprove={(id) => sendApproval(id, true)}
                onReject={(id) => sendApproval(id, false)}
              />
            )}
            {tab === 'flow' && <AgentFlowPanel flow={agentFlow} toolCalls={toolCalls} />}
            {tab === 'history' && <HistoryPanel history={history} onClear={clearHistory} />}
          </div>
        </div>
      </div>
    </>
  )
}
