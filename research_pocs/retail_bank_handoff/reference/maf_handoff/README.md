# Native MAF Handoff Reference

The clean Microsoft Agent Framework reference samples are retained without modification in:

- `../../../MAF-Multi-Agent-Patterns-Python/03-HandoffAgents/handoff_specialist_to_specialist.py`
- `../../../MAF-Multi-Agent-Patterns-Python/03-HandoffAgents/handoff_return_to_previous.py`

The retail-bank workflow follows the same native `HandoffBuilder` pattern. It retains
the open participant mesh so MAF supplies `handoff_to_<agent>` tools automatically.
Triage and specialists derive unresolved domains from the original request and the
completed tool results in the shared conversation. Every specialist completes one
domain and uses a native handoff tool for the next agent. The final specialist returns
to triage, which produces the consolidated response.

The installed runtime is `agent-framework 1.8.1` with
`agent-framework-orchestrations 1.0.0rc3`. It supports `.with_start_agent()` and
`.add_handoff()`, which the retail POC uses. It does not expose the newer sample APIs
`.set_coordinator()` or `.enable_return_to_previous()`. It does expose
`.with_autonomous_mode()`, which the retail workflow uses to publish each domain
tool result before the next native handoff.

`validate_multi_intent.py` runs the retail workflow against configured model
credentials and reports native MAF agent activations, handoffs, completed domain
tools, and the final triage response.