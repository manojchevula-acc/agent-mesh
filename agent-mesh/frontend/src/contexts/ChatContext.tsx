import { createContext, useContext } from "react";

interface ChatContextValue {
  clearChat: () => void;
}

export const ChatContext = createContext<ChatContextValue>({ clearChat: () => {} });
export const useChatContext = () => useContext(ChatContext);
