// Hand-drawn art for the empty states (AsciiEmptyState), 6 to 10 rows each, in
// characters Geist Mono draws at one width. Decoration only: always aria-hidden, with
// the real words beside it.

export const ART = {
  brain: [
    "     .-~~~~~~-.     ",
    "   .'  ~.  .~ '.    ",
    "  /  .~  ~~  ~. \\   ",
    " |  ~  .~~~.   ~ |  ",
    " |  .~'     '~.  |  ",
    "  \\  '~.  .~'   /   ",
    "   '-.__.--.__.'    ",
    "        |  |        ",
  ],
  folder: [
    "  ______            ",
    " |      \\_________  ",
    " |                | ",
    " |    · · · ·     | ",
    " |                | ",
    " |________________| ",
  ],
  plug: [
    "      |   |       ",
    "     _|___|_      ",
    "    |       |     ",
    "    |  · ·  |     ",
    "     \\_____/      ",
    "        |         ",
    "     ~~~'         ",
  ],
  key: [
    "    .-----.              ",
    "   /  .-.  \\             ",
    "  |  |   |  |___________ ",
    "  |   '-'   |---|-|-|-|' ",
    "   \\       /             ",
    "    '-----'              ",
  ],
  search: [
    "    .----.      ",
    "   /  ..  \\     ",
    "  |  .  .  |    ",
    "   \\  ..  /     ",
    "    '----'\\     ",
    "           \\\\   ",
    "            \\\\  ",
  ],
  inbox: [
    "  ________________  ",
    " /                \\ ",
    "|                  |",
    "|______.    .______|",
    "|      '----'      |",
    "|__________________|",
  ],
  broken: [
    "  .--------------.  ",
    "  |  x        x  |  ",
    "  |      /\\      |  ",
    "  |   .-'  '-.   |  ",
    "  '------/\\------'  ",
    "        /  \\        ",
  ],
} as const satisfies Record<string, readonly string[]>

export type ArtName = keyof typeof ART
