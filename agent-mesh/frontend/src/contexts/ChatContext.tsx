import React, { createContext, useCallback, useContext, useRef } from "react";

interface ChatContextValue {
  clearChat: () => void;
  registerClearChat: (fn: () => void) => void;
}

export const ChatContext = createContext<ChatContextValue>({
  clearChat: () => {},
  registerClearChat: () => {},
});

export const useChatContext = () => useContext(ChatContext);

/** Wrap AppLayout (or the router root) with this so clearChat works from the sidebar. */
export function ChatContextProvider({ children }: { children: React.ReactNode }) {
  const ref = useRef<() => void>(() => {});
  const clearChat = useCallback(() => ref.current(), []);
  const registerClearChat = useCallback((fn: () => void) => { ref.current = fn; }, []);
  return (
    <ChatContext.Provider value={{ clearChat, registerClearChat }}>
      {children}
    </ChatContext.Provider>
  );
}
