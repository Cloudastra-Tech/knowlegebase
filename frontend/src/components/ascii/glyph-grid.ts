import { measureCell } from "@/lib/ascii-loop"

// A canvas that shows a grid of monospace cells, for the landing's diagrams (the
// model's route, the sources' network, the closing band's field). A scene writes the
// frame it wants into `next` (put, text) and flush() redraws only the cells that
// changed since the last flush, from an atlas of glyphs drawn once each (a glyph in
// a tone is drawn the first time it's needed). So a frame where a few characters move
// costs a few drawImage calls, whatever the size of the grid: the approach of the
// brain and the empty chat's drift (ascii-brain.tsx, redacted-drift.tsx).
//
// Tones are CSS colours (any expression, var() included), resolved against the host:
// index 0 is never drawn (an empty cell). The host's font is the grid's.

const SLOTS = 32 // atlas slots per row

// The box-drawing characters the diagrams use, as the arms each one has.
type Arms = { l?: boolean; r?: boolean; u?: boolean; d?: boolean; dash?: boolean }
const BOX: Record<string, Arms> = {
  "─": { l: true, r: true },
  "│": { u: true, d: true },
  "┌": { r: true, d: true },
  "┐": { l: true, d: true },
  "└": { r: true, u: true },
  "┘": { l: true, u: true },
  "├": { r: true, u: true, d: true },
  "┤": { l: true, u: true, d: true },
  "┬": { l: true, r: true, d: true },
  "┴": { l: true, r: true, u: true },
  "┼": { l: true, r: true, u: true, d: true },
  "┄": { r: true, dash: true },
  "┆": { d: true, dash: true },
}

export class GlyphGrid {
  cols = 0
  rows = 0
  cw = 1 // a cell, in CSS px
  ch = 1
  private next = new Uint32Array(0) // per cell: char code << 8 | tone; 0 is empty
  private shown = new Uint32Array(0)
  private dpr = 1
  private xs = new Int32Array(1) // each column's left edge, in device px
  private ys = new Int32Array(1)
  private ctx: CanvasRenderingContext2D | null
  private atlas = document.createElement("canvas")
  private actx = this.atlas.getContext("2d")
  private slots = new Map<number, number>()
  private colours: string[] = []
  private aw = 1
  private ah = 1
  private host: HTMLElement
  private canvas: HTMLCanvasElement
  private tones: readonly string[]

  constructor(host: HTMLElement, canvas: HTMLCanvasElement, tones: readonly string[]) {
    this.host = host
    this.canvas = canvas
    this.tones = tones
    this.ctx = canvas.getContext("2d", { alpha: true })
    this.measure()
  }

  // The grid fills the host (or `cols` x `rows`, when given), cells rounded to whole
  // device px so clearing one never leaves a seam.
  measure(size?: { cols: number; rows: number }) {
    const cell = measureCell(this.host)
    this.cw = cell.w || 7
    this.ch = cell.h || 13
    this.dpr = Math.min(2, window.devicePixelRatio || 1)
    this.cols = Math.max(1, size?.cols ?? Math.floor(this.host.clientWidth / this.cw))
    this.rows = Math.max(1, size?.rows ?? Math.floor(this.host.clientHeight / this.ch))
    this.xs = Int32Array.from({ length: this.cols + 1 }, (_, c) => Math.round(c * this.cw * this.dpr))
    this.ys = Int32Array.from({ length: this.rows + 1 }, (_, r) => Math.round(r * this.ch * this.dpr))
    this.canvas.width = this.xs[this.cols]
    this.canvas.height = this.ys[this.rows]
    this.canvas.style.width = `${this.canvas.width / this.dpr}px`
    this.canvas.style.height = `${this.canvas.height / this.dpr}px`
    this.next = new Uint32Array(this.cols * this.rows)
    this.shown = new Uint32Array(this.cols * this.rows)
    this.restyle()
  }

  // The tones and the font again (a theme change, a new size): every glyph is drawn anew.
  restyle() {
    const s = getComputedStyle(this.host)
    const probe = document.createElement("span")
    this.host.appendChild(probe)
    this.colours = this.tones.map((tone) => {
      probe.style.color = ""
      probe.style.color = tone
      return getComputedStyle(probe).color
    })
    probe.remove()
    this.aw = Math.ceil(this.cw * this.dpr) + 2
    this.ah = Math.ceil(this.ch * this.dpr) + 2
    this.slots.clear()
    this.atlas.width = this.aw * SLOTS
    this.atlas.height = this.ah * 4
    if (this.actx) {
      this.actx.font = `${s.fontWeight} ${parseFloat(s.fontSize) * this.dpr}px ${s.fontFamily}`
      this.actx.textAlign = "center"
      this.actx.textBaseline = "middle"
    }
    this.shown.fill(0xffffffff) // unknown: draw every cell at the next flush
  }

  clear() {
    this.next.fill(0)
  }

  put(c: number, r: number, char: string, tone: number) {
    if (c < 0 || r < 0 || c >= this.cols || r >= this.rows || !tone) return
    const code = char.charCodeAt(0)
    this.next[r * this.cols + c] = code === 32 ? 0 : ((code << 8) | tone) >>> 0
  }

  text(c: number, r: number, str: string, tone: number) {
    for (let i = 0; i < str.length; i++) this.put(c + i, r, str[i], tone)
  }

  get(c: number, r: number) {
    if (c < 0 || r < 0 || c >= this.cols || r >= this.rows) return 0
    return this.next[r * this.cols + c]
  }

  private slot(key: number) {
    let at = this.slots.get(key)
    if (at !== undefined) return at
    at = this.slots.size
    const row = Math.floor(at / SLOTS)
    if ((row + 1) * this.ah > this.atlas.height) {
      // Grow the atlas, keeping what's drawn (setting a size clears a canvas).
      const old = document.createElement("canvas")
      old.width = this.atlas.width
      old.height = this.atlas.height
      old.getContext("2d")?.drawImage(this.atlas, 0, 0)
      const font = this.actx?.font
      this.atlas.height *= 2
      if (this.actx) {
        this.actx.drawImage(old, 0, 0)
        this.actx.font = font ?? ""
        this.actx.textAlign = "center"
        this.actx.textBaseline = "middle"
      }
    }
    if (this.actx) {
      const char = String.fromCharCode(key >>> 8)
      const x = (at % SLOTS) * this.aw
      const y = row * this.ah
      this.actx.fillStyle = this.colours[key & 0xff] ?? "transparent"
      if (!this.box(char, x, y)) this.actx.fillText(char, x + this.aw / 2, y + this.ah / 2)
    }
    this.slots.set(key, at)
    return at
  }

  // Box-drawing characters are drawn as lines to the cell's edges, not from the font:
  // a font's │ is shorter than a cell of line-height 1.15, and a column of them would
  // be a dashed line. Returns false for any other character.
  private box(char: string, x: number, y: number) {
    const arms = BOX[char]
    if (!arms || !this.actx) return false
    const g = this.actx
    const t = Math.max(1, Math.round(this.dpr)) // the line's weight, in device px
    const cx = x + 1 + Math.floor((this.aw - 2 - t) / 2)
    const cy = y + 1 + Math.floor((this.ah - 2 - t) / 2)
    const left = x
    const right = x + this.aw
    const top = y
    const bottom = y + this.ah
    if (arms.dash) {
      // A dashed run: two short dashes a cell.
      const horizontal = arms.r
      const n = 2
      for (let i = 0; i < n; i++) {
        if (horizontal) {
          const w = (this.aw - 2) / n
          g.fillRect(Math.round(x + 1 + i * w + w * 0.2), cy, Math.max(1, Math.round(w * 0.6)), t)
        } else {
          const h = (this.ah - 2) / n
          g.fillRect(cx, Math.round(y + 1 + i * h + h * 0.2), t, Math.max(1, Math.round(h * 0.6)))
        }
      }
      return true
    }
    // Each pixel once: the lines are translucent, and where two arms overlapped the
    // overlap came out darker, a dot in every cell of a run (a dotted line).
    const h = arms.l || arms.r
    if (h) {
      const from = arms.l ? left : cx
      g.fillRect(from, cy, (arms.r ? right : cx + t) - from, t)
    }
    if (arms.u) g.fillRect(cx, top, t, cy - top + (h ? 0 : t))
    if (arms.d) g.fillRect(cx, h || arms.u ? cy + t : cy, t, bottom - (h || arms.u ? cy + t : cy))
    return true
  }

  // Draws what changed since the last flush.
  flush() {
    const ctx = this.ctx
    if (!ctx) return
    const { cols, next, shown, xs, ys, aw, ah } = this
    for (let k = 0; k < next.length; k++) {
      const key = next[k]
      if (key === shown[k]) continue
      const c = k % cols
      const r = (k - c) / cols
      const x = xs[c]
      const y = ys[r]
      const w = xs[c + 1] - x
      const h = ys[r + 1] - y
      ctx.clearRect(x, y, w, h)
      if (key) {
        const at = this.slot(key)
        // The glyph's centre on the cell's centre (the atlas slot is a pixel larger all round).
        ctx.drawImage(this.atlas, (at % SLOTS) * aw + 1, Math.floor(at / SLOTS) * ah + 1, w, h, x, y, w, h)
      }
      shown[k] = key
    }
  }
}
