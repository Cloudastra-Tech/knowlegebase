// Rules about the app's URLs, kept in one place for the screens that ask them.

// The chat: a new one ("/chat") or a saved one ("/chat/<id>"), not "/chats…".
export function isChatPath(path: string): boolean {
  return path === "/chat" || path.startsWith("/chat/")
}
