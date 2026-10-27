// The support tickets / conversations a connector can also sync (its check_options):
// off by default, and while on the connection is locked to HR and admins.
export const TICKET_OPTIONS: Record<
  string,
  { key: "tickets" | "conversations"; label: string; toggle: string; description: string }
> = {
  zendesk: {
    key: "tickets",
    label: "Solved tickets",
    toggle: "Also sync solved tickets",
    description:
      "Solved and closed tickets with their public replies. They hold customers' details, so while this is on only HR and admins can ask about anything from Zendesk.",
  },
  freshdesk: {
    key: "tickets",
    label: "Resolved tickets",
    toggle: "Also sync resolved tickets",
    description:
      "Resolved and closed tickets with their public replies (private notes stay in Freshdesk). They hold customers' details, so while this is on only HR and admins can ask about anything from Freshdesk.",
  },
  intercom: {
    key: "conversations",
    label: "Closed conversations",
    toggle: "Also sync closed conversations",
    description:
      "Closed conversations with customer and teammate replies (internal notes stay in Intercom). They hold customers' details, so while this is on only HR and admins can ask about anything from Intercom.",
  },
}

// The opposite kind of option: a connection LOCKED to HR and admins until an admin lets
// members ask (Teams channel messages: connectors/teams_messages.py check_options).
export const MEMBER_OPTIONS: Record<
  string,
  { key: "members"; label: string; toggle: string; description: string; locked: string }
> = {
  teams_messages: {
    key: "members",
    label: "Members can ask",
    toggle: "Let members ask",
    description:
      "Off: only HR and admins can ask about Teams messages. On: choose who can on the card, for example each channel's members.",
    locked: "Locked to HR and admins until you let members ask (Edit selection).",
  },
}
