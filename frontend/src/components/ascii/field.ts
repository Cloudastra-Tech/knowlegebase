// The field's pattern, shared by the landing's <pre> art (the brain's background, the
// strip behind the closing call to action) and the app's canvas field.

export const FIELD_RAMP = " ..··::--~~"

// A slow interference pattern, with a ripple spreading from the pointer (px, py; far
// away for none). Returns -4..4 plus the ripple; index() maps it onto a ramp.
export function fieldValue(x: number, y: number, t: number, px: number, py: number) {
  let v =
    Math.sin(x * 0.11 + t * 0.55) +
    Math.sin(y * 0.23 - t * 0.4) +
    Math.sin((x + y * 1.8) * 0.06 + t * 0.3) +
    Math.sin(Math.hypot(x - 60, (y - 20) * 2) * 0.09 - t * 0.8)
  const d = Math.hypot(x - px, (y - py) * 2)
  v += 2.2 * Math.sin(d * 0.5 - t * 5) * Math.exp(-d * 0.07)
  return v
}

export function rampIndex(v: number, length: number) {
  const i = Math.floor(((v + 4) / 8) * length)
  return i < 0 ? 0 : i >= length ? length - 1 : i
}

export function fieldChar(x: number, y: number, t: number, px: number, py: number, ramp = FIELD_RAMP) {
  return ramp[rampIndex(fieldValue(x, y, t, px, py), ramp.length)]
}

// The same pattern for a whole grid at once, for the canvas: everything that doesn't
// change with time is worked out once per size, so a frame is a few multiplications a
// cell and no trigonometry (sin(a + b) = sin a cos b + cos a sin b). The radial term
// is centred on the grid rather than the landing's fixed point.
export type FieldGrid = {
  cols: number
  rows: number
  sinU: Float32Array // the diagonal term's phase per cell
  cosU: Float32Array
  sinD: Float32Array // the radial term's phase per cell
  cosD: Float32Array
}

export function fieldGrid(cols: number, rows: number): FieldGrid {
  const n = cols * rows
  const g = {
    cols,
    rows,
    sinU: new Float32Array(n),
    cosU: new Float32Array(n),
    sinD: new Float32Array(n),
    cosD: new Float32Array(n),
  }
  const cx = cols / 2
  const cy = rows * 0.4
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const k = r * cols + c
      const u = (c + r * 1.8) * 0.06
      const d = Math.hypot(c - cx, (r - cy) * 2) * 0.09
      g.sinU[k] = Math.sin(u)
      g.cosU[k] = Math.cos(u)
      g.sinD[k] = Math.sin(d)
      g.cosD[k] = Math.cos(d)
    }
  }
  return g
}

// Fills `out` with ramp indices for time t. `ripple`, when given, adds one ring
// spreading from (x, y) in cells, `age` seconds old (the thinking pulse); `pointer`,
// the landing's ripple that follows the pointer (as fieldValue's), in cells.
export function fillField(
  g: FieldGrid,
  t: number,
  length: number,
  out: Uint8Array,
  colTerm: Float32Array,
  ripple?: { x: number; y: number; age: number },
  pointer?: { x: number; y: number },
) {
  const { cols, rows } = g
  for (let c = 0; c < cols; c++) colTerm[c] = Math.sin(c * 0.11 + t * 0.55)
  const s3 = Math.sin(t * 0.3)
  const c3 = Math.cos(t * 0.3)
  const s4 = Math.sin(t * 0.8)
  const c4 = Math.cos(t * 0.8)
  const scale = length / 8
  for (let r = 0; r < rows; r++) {
    const rowTerm = Math.sin(r * 0.23 - t * 0.4)
    const base = r * cols
    for (let c = 0; c < cols; c++) {
      const k = base + c
      let v =
        colTerm[c] +
        rowTerm +
        (g.sinU[k] * c3 + g.cosU[k] * s3) + // sin(u + 0.3t)
        (g.sinD[k] * c4 - g.cosD[k] * s4) // sin(d - 0.8t)
      if (ripple) {
        const d = Math.hypot(c - ripple.x, (r - ripple.y) * 2)
        const front = d - ripple.age * 40
        v += 2.4 * Math.exp(-(front * front) / 18) * (1 - ripple.age / 1.2)
      }
      if (pointer) {
        const d = Math.hypot(c - pointer.x, (r - pointer.y) * 2)
        if (d < 60) v += 2.2 * Math.sin(d * 0.5 - t * 5) * Math.exp(-d * 0.07)
      }
      const i = Math.floor((v + 4) * scale)
      out[k] = i < 0 ? 0 : i >= length ? length - 1 : i
    }
  }
}
