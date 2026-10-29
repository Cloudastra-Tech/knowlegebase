// Something drawn into the brain's grid each frame, where the brain itself isn't (the
// landing's hero streams documents into it: landing-hero.tsx). Cells in the brain's
// own units; `brain(c, r)` says whether the brain covers a cell. The brain never asks
// for its still frame.
export type BrainGrid = {
  cols: number
  rows: number
  cx: number // the brain's centre, in cells
  cy: number
  rx: number // its radius, in cells across and down
  ry: number
  wide: boolean // beside the headline (else above it)
  brain(c: number, r: number): boolean
  put(c: number, r: number, glyph: string, signal: boolean): void
}

// The brain (ascii-brain.tsx, `overlay`) calls draw() each frame; a scene sets what it
// draws, or nothing.
export class BrainOverlay {
  private fn: ((g: BrainGrid) => void) | null = null
  set(fn: ((g: BrainGrid) => void) | null) {
    this.fn = fn
  }
  draw(g: BrainGrid) {
    this.fn?.(g)
  }
  get active() {
    return this.fn !== null
  }
}
