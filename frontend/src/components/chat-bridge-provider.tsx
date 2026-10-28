import { useMemo, useRef, useState, type ReactNode } from "react"

import {
  ChatPortContext,
  ChatStateContext,
  INITIAL_CHAT_STATE,
  type ChatActions,
  type ChatPort,
} from "@/lib/chat-bridge"

// Between the chat and the app's sidebar (lib/chat-bridge.ts).
export function ChatBridgeProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(INITIAL_CHAT_STATE)
  const actions = useRef<ChatActions | null>(null)
  const port = useMemo<ChatPort>(
    () => ({
      setState,
      setActions: (next) => {
        actions.current = next
      },
      actions: () => actions.current,
    }),
    [],
  )
  return (
    <ChatPortContext.Provider value={port}>
      <ChatStateContext.Provider value={state}>{children}</ChatStateContext.Provider>
    </ChatPortContext.Provider>
  )
}
