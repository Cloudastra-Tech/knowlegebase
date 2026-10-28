import { useMemo, useState, type ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ChevronRight,
  Folder,
  FolderInput,
  FolderPlus,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Share2,
  Trash2,
} from "lucide-react"
import { toast } from "sonner"

import { ShareDialog } from "@/components/share-dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  SidebarMenuSub,
  useSidebar,
} from "@/components/ui/sidebar"
import { api, type ChatFolder, type ConversationSummary } from "@/lib/api"
import { groupChats } from "@/lib/chat-history"
import { queries } from "@/lib/queries"

// A name to give: renaming a chat or a folder, or a new folder.
function NameDialog({
  open,
  onOpenChange,
  title,
  label,
  initial,
  maxLength,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  label: string
  initial: string
  maxLength: number
  onSave: (name: string) => Promise<void>
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        {/* Mounted while open, so it starts from `initial` each time. */}
        <NameForm
          label={label}
          initial={initial}
          maxLength={maxLength}
          onSave={onSave}
          onDone={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  )
}

function NameForm({
  label,
  initial,
  maxLength,
  onSave,
  onDone,
}: {
  label: string
  initial: string
  maxLength: number
  onSave: (name: string) => Promise<void>
  onDone: () => void
}) {
  const [name, setName] = useState(initial)
  const [saving, setSaving] = useState(false)
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault()
        if (!name.trim()) return
        setSaving(true)
        try {
          await onSave(name)
          onDone()
        } catch (err) {
          toast.error(err instanceof Error ? err.message : "Couldn't save it.")
        } finally {
          setSaving(false)
        }
      }}
    >
      <Input
        value={name}
        onChange={(e) => setName(e.target.value)}
        aria-label={label}
        maxLength={maxLength}
        autoFocus
      />
      <DialogFooter className="mt-4">
        <Button type="submit" loading={saving} disabled={!name.trim()}>
          Save
        </Button>
      </DialogFooter>
    </form>
  )
}

type ChatAction =
  | { kind: "rename"; chat: ConversationSummary }
  | { kind: "delete"; chat: ConversationSummary }
  | { kind: "share"; chat: ConversationSummary }
  | { kind: "new-folder" }
  | { kind: "rename-folder"; folder: ChatFolder }
  | { kind: "delete-folder"; folder: ChatFolder }

// One chat in the list, with its menu: Rename, Pin, Move to folder, Share, Delete.
function ChatRow({
  chat,
  active,
  folders,
  busy,
  onOpen,
  onAction,
  onOrganize,
}: {
  chat: ConversationSummary
  active: boolean
  folders: ChatFolder[]
  busy: boolean
  onOpen: () => void
  onAction: (a: ChatAction) => void
  onOrganize: (edit: { pinned?: boolean; folder_id?: string | null }) => void
}) {
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        isActive={active}
        onClick={onOpen}
        aria-current={active ? "page" : undefined}
        className="pr-8 pointer-coarse:h-10"
      >
        <span className="truncate">{chat.title}</span>
      </SidebarMenuButton>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuAction
            showOnHover
            aria-label={`Options for ${chat.title}`}
            className="pointer-coarse:top-2.5 pointer-coarse:opacity-100"
          >
            <MoreHorizontal />
          </SidebarMenuAction>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="start" className="w-48">
          <DropdownMenuItem onSelect={() => onAction({ kind: "rename", chat })}>
            <Pencil /> Rename
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => onOrganize({ pinned: !chat.pinned })}>
            {chat.pinned ? <PinOff /> : <Pin />} {chat.pinned ? "Unpin" : "Pin"}
          </DropdownMenuItem>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <FolderInput /> Move to folder
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-44">
              {folders.map((f) => (
                <DropdownMenuItem
                  key={f.id}
                  disabled={chat.folder_id === f.id}
                  onSelect={() => onOrganize({ folder_id: f.id })}
                >
                  <Folder /> <span className="truncate">{f.name}</span>
                </DropdownMenuItem>
              ))}
              {chat.folder_id && (
                <DropdownMenuItem onSelect={() => onOrganize({ folder_id: null })}>
                  No folder
                </DropdownMenuItem>
              )}
              {folders.length > 0 && <DropdownMenuSeparator />}
              <DropdownMenuItem onSelect={() => onAction({ kind: "new-folder" })}>
                <FolderPlus /> New folder
              </DropdownMenuItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuItem onSelect={() => onAction({ kind: "share", chat })}>
            <Share2 /> Share
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            disabled={busy && active}
            onSelect={() => onAction({ kind: "delete", chat })}
          >
            <Trash2 /> Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarMenuItem>
  )
}

function Group({ label, action, children }: { label: string; action?: ReactNode; children: ReactNode }) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>{label}</SidebarGroupLabel>
      {action}
      <SidebarGroupContent>
        <SidebarMenu>{children}</SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

// The chat history (C9) in the app's sidebar (app-sidebar.tsx): Pinned, Folders, then
// the rest by when they were last used. Not in the collapsed rail.
export function ChatHistory({
  currentId,
  onChat,
  busy,
  onOpen,
  onDeleted,
}: {
  currentId: string | null // the conversation the chat is at (also from another screen)
  onChat: boolean // the chat is the open screen: its conversation is marked
  busy: boolean // an answer is being written in that conversation
  onOpen: (id: string) => void
  onDeleted: (id: string) => void
}) {
  const queryClient = useQueryClient()
  const { isMobile, setOpenMobile } = useSidebar()
  const list = useQuery(queries.conversations())
  const [action, setAction] = useState<ChatAction | null>(null)
  // The folder a new folder is made for: this chat moves into it.
  const [moveAfterCreate, setMoveAfterCreate] = useState<ConversationSummary | null>(null)

  const chats = list.data?.conversations
  const folders = useMemo(() => list.data?.folders ?? [], [list.data])
  const grouped = useMemo(() => groupChats(chats ?? []), [chats])
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["conversations"] })

  function open(id: string) {
    onOpen(id)
    if (isMobile) setOpenMobile(false)
  }

  async function organize(chat: ConversationSummary, edit: { pinned?: boolean; folder_id?: string | null }) {
    try {
      await api.updateConversation(chat.id, edit)
      refresh()
      if (edit.pinned !== undefined) toast.success(edit.pinned ? "Pinned." : "Unpinned.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't change it.")
    }
  }

  async function remove(chat: ConversationSummary) {
    try {
      await api.deleteConversation(chat.id)
      refresh()
      toast.success("Chat deleted.")
      onDeleted(chat.id)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't delete it.")
    }
  }

  async function removeFolder(folder: ChatFolder) {
    try {
      await api.deleteFolder(folder.id)
      refresh()
      toast.success("Folder deleted. Its chats are still here.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't delete it.")
    }
  }

  const row = (chat: ConversationSummary) => (
    <ChatRow
      key={chat.id}
      chat={chat}
      active={onChat && chat.id === currentId}
      folders={folders}
      busy={busy && chat.id === currentId}
      onOpen={() => open(chat.id)}
      onAction={(a) => {
        if (a.kind === "new-folder") setMoveAfterCreate(chat)
        setAction(a)
      }}
      onOrganize={(edit) => organize(chat, edit)}
    />
  )

  return (
    <>
      {list.isPending ? (
        <Group label="Chats">
          {Array.from({ length: 5 }, (_, i) => (
            <SidebarMenuItem key={i}>
              <SidebarMenuSkeleton />
            </SidebarMenuItem>
          ))}
        </Group>
      ) : (
        <>
          {grouped.pinned.length > 0 && <Group label="Pinned">{grouped.pinned.map(row)}</Group>}
          <Group
            label="Folders"
            action={
              <SidebarGroupAction
                title="New folder"
                aria-label="New folder"
                onClick={() => {
                  setMoveAfterCreate(null)
                  setAction({ kind: "new-folder" })
                }}
              >
                <FolderPlus />
              </SidebarGroupAction>
            }
          >
            {folders.length === 0 && (
              <SidebarMenuItem>
                <p className="px-2 text-xs text-muted-foreground">Group chats in folders from their menu.</p>
              </SidebarMenuItem>
            )}
            {folders.map((folder) => {
              const inside = grouped.byFolder.get(folder.id) ?? []
              return (
                <Collapsible key={folder.id} defaultOpen className="group/collapsible">
                  <SidebarMenuItem>
                    <CollapsibleTrigger asChild>
                      <SidebarMenuButton className="pr-8 pointer-coarse:h-10">
                        <ChevronRight className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
                        <Folder />
                        <span className="truncate">{folder.name}</span>
                        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                          {inside.length}
                        </span>
                      </SidebarMenuButton>
                    </CollapsibleTrigger>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <SidebarMenuAction
                          showOnHover
                          aria-label={`Options for folder ${folder.name}`}
                          className="pointer-coarse:opacity-100"
                        >
                          <MoreHorizontal />
                        </SidebarMenuAction>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent side="right" align="start">
                        <DropdownMenuItem onSelect={() => setAction({ kind: "rename-folder", folder })}>
                          <Pencil /> Rename
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          variant="destructive"
                          onSelect={() => setAction({ kind: "delete-folder", folder })}
                        >
                          <Trash2 /> Delete folder
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                    <CollapsibleContent>
                      <SidebarMenuSub className="mr-0 pr-0">
                        {inside.length === 0 ? (
                          <p className="px-2 py-1 text-xs text-muted-foreground">Empty</p>
                        ) : (
                          inside.map(row)
                        )}
                      </SidebarMenuSub>
                    </CollapsibleContent>
                  </SidebarMenuItem>
                </Collapsible>
              )
            })}
          </Group>
          {grouped.dated.map((g) => (
            <Group key={g.label} label={g.label}>
              {g.items.map(row)}
            </Group>
          ))}
          {chats?.length === 0 && (
            <p className="px-4 py-2 text-sm text-muted-foreground">Your chats show up here.</p>
          )}
        </>
      )}

      <NameDialog
        open={action?.kind === "rename"}
        onOpenChange={(o) => !o && setAction(null)}
        title="Rename chat"
        label="Chat name"
        maxLength={80}
        initial={action?.kind === "rename" ? action.chat.title : ""}
        onSave={async (name) => {
          if (action?.kind !== "rename") return
          await api.updateConversation(action.chat.id, { title: name })
          refresh()
        }}
      />
      <NameDialog
        open={action?.kind === "new-folder" || action?.kind === "rename-folder"}
        onOpenChange={(o) => !o && setAction(null)}
        title={action?.kind === "rename-folder" ? "Rename folder" : "New folder"}
        label="Folder name"
        maxLength={60}
        initial={action?.kind === "rename-folder" ? action.folder.name : ""}
        onSave={async (name) => {
          if (action?.kind === "rename-folder") {
            await api.renameFolder(action.folder.id, name)
          } else {
            const folder = await api.createFolder(name)
            if (moveAfterCreate) await api.updateConversation(moveAfterCreate.id, { folder_id: folder.id })
          }
          refresh()
        }}
      />
      <AlertDialog open={action?.kind === "delete"} onOpenChange={(o) => !o && setAction(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this chat?</AlertDialogTitle>
            <AlertDialogDescription>
              “{action?.kind === "delete" ? action.chat.title : ""}”, its answers, any feedback you
              gave on them and its share links are deleted for good.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => action?.kind === "delete" && remove(action.chat)}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog open={action?.kind === "delete-folder"} onOpenChange={(o) => !o && setAction(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this folder?</AlertDialogTitle>
            <AlertDialogDescription>The chats in it aren't deleted: they move out of it.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => action?.kind === "delete-folder" && removeFolder(action.folder)}
            >
              Delete folder
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {action?.kind === "share" && (
        <ShareDialog
          open
          onOpenChange={(o) => !o && setAction(null)}
          conversationId={action.chat.id}
        />
      )}
    </>
  )
}
