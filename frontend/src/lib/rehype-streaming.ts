// A rehype plugin for an answer while it streams in (components/chat-markdown.tsx):
// - each word becomes a <span class="kb-word">, which fades in once, when it mounts.
//   The server sends the whole answer so far each time, and React keeps the spans
//   already drawn, so only the new words fade; a word changed earlier on (masked, a
//   marker turned into a chip) changes in place, without fading again.
// - a caret (<span class="kb-caret">▌</span>) goes inline after the last word.
// Code is left alone. Once the answer is done the plugin is off: plain text again,
// with no spans, so the final, checked answer swaps in without anything moving.
import type { Element, ElementContent, Root, RootContent } from "hast"

const SKIP = new Set(["code", "pre", "a"])
// Blocks the caret may go inside (after their last word), and blocks to look into.
const HOSTS = new Set(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th"])
const CONTAINERS = new Set(["ul", "ol", "blockquote", "table", "thead", "tbody", "tr", "li"])

function words(value: string): ElementContent[] {
  const out: ElementContent[] = []
  for (const part of value.split(/(\s+)/)) {
    if (!part) continue
    out.push(
      /^\s+$/.test(part)
        ? { type: "text", value: part }
        : {
            type: "element",
            tagName: "span",
            properties: { className: ["kb-word"] },
            children: [{ type: "text", value: part }],
          },
    )
  }
  return out
}

function split(node: Root | Element) {
  const next: (RootContent | ElementContent)[] = []
  for (const child of node.children) {
    if (child.type === "text") next.push(...words(child.value))
    else {
      if (child.type === "element" && !SKIP.has(child.tagName)) split(child)
      next.push(child)
    }
  }
  node.children = next as typeof node.children
}

const isElement = (n: RootContent | ElementContent): n is Element => n.type === "element"
const blank = (n: RootContent | ElementContent) => n.type === "text" && !n.value.trim()

function caretHost(root: Root): Root | Element {
  let at: Root | Element = root
  for (;;) {
    const last: RootContent | ElementContent | undefined = [...at.children].reverse().find((c) => !blank(c))
    if (!last || !isElement(last) || !(HOSTS.has(last.tagName) || CONTAINERS.has(last.tagName))) break
    at = last
  }
  return at
}

export function rehypeStreaming() {
  return (tree: Root) => {
    split(tree)
    const caret: Element = {
      type: "element",
      tagName: "span",
      properties: { className: ["kb-caret"], ariaHidden: "true" },
      children: [{ type: "text", value: "▌" }],
    }
    const host = caretHost(tree)
    // Inside a paragraph or a list item: inline after the last word. Anywhere else
    // (after a table, a code block), on its own line below it.
    if (host.type === "element" && HOSTS.has(host.tagName)) host.children.push(caret)
    else tree.children.push(caret)
  }
}
