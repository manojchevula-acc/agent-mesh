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