import { createContext, useContext } from "react"

// The chat and the app's sidebar (components/app-sidebar.tsx), which are apart: the
// sidebar is on every screen, and the chat stays mounted behind the others (App.tsx).
// The chat tells the sidebar which conversation it's at, the space it asks in and
// whether an answer is being written; the sidebar asks the chat to open a chat, start
// a new one or change the space, so an answer being written stops first, as it does
// from inside the chat. Two contexts: the chat only writes, so it doesn't draw again
// when what it wrote changes. The provider is components/chat-bridge-provider.tsx.

export interface ChatState {
  // The conversation the chat is at, also while another screen is open (null: a new one).
  conversationId: string | null
  spaceId: string | null
  busy: boolean // an answer is being written
}

export interface ChatActions {
  open: (id: string) => void
  newChat: () => void
  chooseSpace: (spaceId: string | null) => void
  deleted: (id: string) => void
}

export interface ChatPort {
  setState: (state: ChatState) => void
  setActions: (actions: ChatActions) => void
  // The chat's actions, null until its code has loaded (the sidebar navigates instead).
  actions: () => ChatActions | null
}

export const INITIAL_CHAT_STATE: ChatState = { conversationId: null, spaceId: null, busy: false }

export const ChatStateContext = createContext<ChatState>(INITIAL_CHAT_STATE)
export const ChatPortContext = createContext<ChatPort | null>(null)

// The sidebar's side: what the chat is at.
export function useChatState() {
  return useContext(ChatStateContext)
}

// Both sides: the chat writes through it, the sidebar reads its actions. Null outside
// the app (a test rendering a piece alone).
export function useChatPort() {
  return useContext(ChatPortContext)
}
