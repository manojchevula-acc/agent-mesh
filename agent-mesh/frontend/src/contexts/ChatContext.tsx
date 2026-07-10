import { createContext, useContext } from "react";
import type { ChatMessage, ConversationSummary } from "@/types/mesh";

interface ChatContextValue {
  messages: ChatMessage[];
  sessionId: string | null;
  sendMessage: (query: string) => void;
  clearChat: () => void;
  switchSession: (sessionId: string) => void;
  handleFeedback: (messageId: string, rating: "up" | "down", comment?: string) => void;
  isLoading: boolean;
  sessions: ConversationSummary[];
}

const noop = () => {};

export const ChatContext = createContext<ChatContextValue>({
  messages: [],
  sessionId: null,
  sendMessage: noop,
  clearChat: noop,
  switchSession: noop,
  handleFeedback: noop,
  isLoading: false,
  sessions: [],
});
export const useChatContext = () => useContext(ChatContext);
