import { lazy, Suspense, type ReactNode } from "react"
import { ChevronRight } from "lucide-react"
import { Link, useNavigate } from "react-router"

import { ScreenHeader } from "@/components/screen-header"
import { ChatsSettings } from "@/components/settings/chats-settings"
import { GeneralSettings } from "@/components/settings/general-settings"
import { SecuritySettings } from "@/components/settings/security-settings"
import { SettingsSkeleton } from "@/components/settings/settings-section"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useIsMobile } from "@/hooks/use-mobile"
import { markInstantNav } from "@/lib/instant-nav"
import { useUser } from "@/lib/auth-context"
import {
  settingsPath,
  settingsSection,
  settingsSectionsFor,
  type SettingsSectionId,
  type SettingsSectionInfo,
} from "@/lib/settings-sections"

// Each section that has a lot of code is its own download, as its screen was.
const MyConnectionsView = lazy(() =>
  import("@/components/my-connections-view").then((m) => ({ default: m.MyConnectionsView })),
)
const PromptsView = lazy(() => import("@/components/prompts-view").then((m) => ({ default: m.PromptsView })))
const DigestsView = lazy(() => import("@/components/digests-view").then((m) => ({ default: m.DigestsView })))
const ApiKeysView = lazy(() => import("@/components/api-keys-view").then((m) => ({ default: m.ApiKeysView })))
const BillingView = lazy(() => import("@/components/billing-view").then((m) => ({ default: m.BillingView })))

// /settings/<section>: everything personal in one screen, as on ChatGPT and Claude.ai.
// From md up, the sections are tabs across the top (one sidebar on screen is enough,
// and the Access screen's tabs work the same way); on a phone,
// /settings is the list of them and a section is a page of its own, with "← Settings"
// in its breadcrumbs' place. Switching sections is instant: no slide, no fade (it's
// done many times in a row); the screen itself arrives as every screen does.
export function SettingsView({
  section,
  openPickerFor,
}: {
  section?: SettingsSectionId
  openPickerFor?: string | null
}) {
  const user = useUser()
  const isMobile = useIsMobile()
  const sections = settingsSectionsFor(user)
  const open = settingsSection(section)
  const listOnly = isMobile && !open

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <ScreenHeader title={isMobile && open ? open.label : "Settings"} />
      {listOnly ? (
        <SectionList sections={sections} />
      ) : (
        <div className="mt-4 md:mt-6">
          {!isMobile && <SectionTabs sections={sections} current={section} />}
          <div className="min-w-0 md:mt-6" data-settings-section={section}>
            <Suspense fallback={<SettingsSkeleton label="Loading" />}>
              {section && <Section id={section} openPickerFor={openPickerFor} />}
            </Suspense>
          </div>
        </div>
      )}
    </div>
  )
}

function Section({ id, openPickerFor }: { id: SettingsSectionId; openPickerFor?: string | null }): ReactNode {
  switch (id) {
    case "general":
      return <GeneralSettings />
    case "chats":
      return <ChatsSettings />
    case "connections":
      return <MyConnectionsView openPickerFor={openPickerFor} />
    case "prompts":
      return <PromptsView />
    case "digests":
      return <DigestsView />
    case "api-keys":
      return <ApiKeysView />
    case "billing":
      return <BillingView />
    case "security":
      return <SecuritySettings />
  }
}

const TAB_KEYS = new Set(["ArrowLeft", "ArrowRight", "Home", "End"])

// From md up: the sections as tabs over the open one. Each is its own address, so a
// tab navigates; arrow keys move between them without a fade.
function SectionTabs({ sections, current }: { sections: SettingsSectionInfo[]; current?: string }) {
  const navigate = useNavigate()
  return (
    <Tabs
      panels={false}
      value={current ?? ""}
      onValueChange={(v) => navigate(settingsPath(v as SettingsSectionId))}
      className="max-w-full"
    >
      <TabsList aria-label="Settings" onKeyDownCapture={(e) => TAB_KEYS.has(e.key) && markInstantNav()}>
        {sections.map((s) => {
          const Icon = s.icon
          return (
            <TabsTrigger key={s.id} value={s.id}>
              <Icon data-icon="inline-start" />
              {s.label}
            </TabsTrigger>
          )
        })}
      </TabsList>
    </Tabs>
  )
}

// On a phone: the sections, as rows that open each one.
function SectionList({ sections }: { sections: SettingsSectionInfo[] }) {
  return (
    <nav aria-label="Settings" className="mt-6">
      <ul className="divide-y overflow-hidden rounded-2xl border bg-card text-card-foreground">
        {sections.map((s) => {
          const Icon = s.icon
          return (
            <li key={s.id}>
              <Link
                to={settingsPath(s.id)}
                className="flex min-h-12 items-center gap-3 px-4 py-3 text-sm outline-none transition-colors duration-(--dur-hover) ease-hover focus-visible:bg-muted active:bg-muted"
              >
                <Icon className="size-4 shrink-0 text-muted-foreground" />
                <span className="flex-1">{s.label}</span>
                <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
              </Link>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
