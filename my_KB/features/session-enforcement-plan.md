# Plan: Session Ownership Enforcement

## Context

When Bob is logged in and navigates to `/app/chat/farida_e3aafcf3` (via the Resume button or directly), he can:
1. Load Farida's full conversation history
2. Send new messages that continue Farida's session thread

This happens because ownership checks exist in the code but are never actually triggered:

- `GET /api/conversations/{session_id}` has a `check_owner()` call, but only runs it `if requesting_user` — and the frontend never sends `?username=`, so the guard is always bypassed.
- `POST /api/query` and `/api/query/stream` have **no ownership check at all** — any user can inject any `session_id`.
- `GET /api/conversations/list` returns all sessions from all users with no filter.
- The Conversations Dashboard shows every user's sessions and the Resume button works for all of them.

**Important caveat:** The auth system is self-reported (no tokens/passwords — it's a demo). So ownership checks prevent *accidental* cross-session access, not deliberate spoofing. We will fix the application-level gaps clearly.

---

## Gaps & Fixes

### Gap 1 — `GET /api/conversations/{session_id}`: ownership check bypassed
**File:** `api_server.py` ~line 223

**Fix:** Always require and enforce `username`. Change:
```python
# OLD — skips check when username absent
if requesting_user and not store.check_owner(session_id, requesting_user):

# NEW — always enforce when username present; make it mandatory
requesting_user = request.query_params.get("username", "").strip()
if not requesting_user or not store.check_owner(session_id, requesting_user):
    return JSONResponse({"error": "Access denied."}, status_code=403)
```

---

### Gap 2 — `POST /api/query` + `POST /api/query/stream`: no ownership check
**File:** `api_server.py` lines ~124 and ~719

**Fix:** After reading `session_id` from the body, check ownership if session_id is provided:
```python
session_id = str(body.get("session_id", "")).strip() or None
if session_id:
    store = ConversationStore()
    if not store.check_owner(session_id, username):
        return JSONResponse({"error": "Access denied: session belongs to another user."}, status_code=403)
```
Add this block to both the non-streaming query endpoint and the streaming query endpoint, before `handle_request` is called.

---

### Gap 3 — `GET /api/conversations/list`: returns all users' sessions
**File:** `api_server.py` ~line 594

**Fix:** Accept an optional `?username=` query param and filter sessions to only return that user's sessions:
```python
requesting_user = request.query_params.get("username", "").strip()
# In the loop — skip sessions that don't belong to the requesting user:
if requesting_user and user != requesting_user:
    continue
```
`user` is already inferred from the session filename prefix (e.g. `farida_37ce2a8d` → `farida`).

---

### Gap 4 — `mesh.ts`: `getConversation()` never sends `username`
**File:** `frontend/src/api/mesh.ts` line 112

**Fix:** Add `username` parameter and pass it as a query param:
```typescript
export async function getConversation(sessionId: string, username: string): Promise<ConversationHistory> {
  const { data } = await apiClient.get<ConversationHistory>(
    `/api/conversations/${encodeURIComponent(sessionId)}?username=${encodeURIComponent(username)}`,
  );
  return data;
}
```

Also update `getConversations` to pass `?username=` for the list endpoint:
```typescript
export async function getConversations(username: string): Promise<ConversationsListResponse> {
  const { data } = await apiClient.get<ConversationsListResponse>(
    `/api/conversations/list?username=${encodeURIComponent(username)}`,
  );
  return data;
}
```

---

### Gap 5 — `useChat.ts`: doesn't pass `username` to `getConversation`, silently ignores 403
**File:** `frontend/src/hooks/useChat.ts` lines 69–94

**Fix:** Pass `username` and surface 403 as a user-visible error:
```typescript
getConversation(sid, username)
  .then((history) => {
    if (cancelled || history.messages.length === 0) return;
    setMessages(history.messages.map(toRestoredMessage));
  })
  .catch((err) => {
    const status = err?.response?.status;
    if (status === 403) {
      // Session belongs to another user — redirect to fresh chat
      sessionIdRef.current = null;
      writeSessionId(null);
      setAccessDenied(true);   // new state flag to show an error banner
    }
    // other errors: start fresh silently
  });
```

Add `accessDenied` to the hook's return value. `ChatPage` renders a banner when set.

---

### Gap 6 — `ConversationsDashboardPage`: shows all sessions, Resume works for everyone
**File:** `frontend/src/pages/ConversationsDashboardPage.tsx`

**Two fixes:**
1. Pass `username` to `getConversations()` so the backend only returns the user's own sessions.
2. In `SessionCard`, disable the Resume button for sessions that don't belong to the logged-in user (as a belt-and-suspenders guard for sessions that slip through).

```typescript
// In the page component — pass username to the query
const { user } = useAuth();
const { data, ... } = useQuery({
  queryKey: ["conversations-list", user?.username],
  queryFn: () => getConversations(user?.username ?? ""),
  ...
});

// In SessionCard — disable Resume if not the owner
const { user } = useAuth();
const isOwner = session.user === user?.username;
<button
  onClick={() => isOwner && navigate(`/app/chat/${session.session_id}`)}
  disabled={!isOwner}
  className={cn("...", !isOwner && "opacity-40 cursor-not-allowed")}
>
  Resume
</button>
```

---

## Files to Change

| File | Changes |
|------|---------|
| `api_server.py` | Gap 1: enforce username in GET conversation; Gap 2: ownership check in both query endpoints; Gap 3: filter list by username |
| `frontend/src/api/mesh.ts` | Gap 4: add username param to `getConversation` and `getConversations` |
| `frontend/src/hooks/useChat.ts` | Gap 5: pass username, handle 403 with `accessDenied` state |
| `frontend/src/pages/ChatPage.tsx` | Show "Access denied" banner when `accessDenied` is true |
| `frontend/src/pages/ConversationsDashboardPage.tsx` | Gap 6: pass username to query, disable Resume for non-owned sessions |

---

## What We Are NOT Doing

Not implementing real authentication (tokens, passwords, JWT). The system is a demo — self-reported usernames. These fixes prevent accidental cross-session access and give clear feedback; they don't prevent a determined attacker who knows the API.

---

## Verification

1. Log in as **farida**, start a conversation → note session_id
2. Log out, log in as **bob**
3. Navigate to `/app/chat/<farida_session_id>` → should see "Access denied" banner, chat empty
4. Open Conversations Dashboard as bob → should only see bob's own sessions (not farida's)
5. Bob sends a message normally → his own new session starts correctly
6. Log back in as farida → her session loads correctly, resume works
