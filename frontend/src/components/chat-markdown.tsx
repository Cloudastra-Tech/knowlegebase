import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"

import "@/components/chat-motion.css"
import { CitationChip } from "@/components/citations"
import { MARKDOWN_BLOCKS } from "@/lib/markdown-blocks"
import { citedNumber, remarkCitations } from "@/lib/citations"
import { rehypeStreaming } from "@/lib/rehype-streaming"
import { cn } from "@/lib/utils"

// Answers render a document's blocks safely (lib/markdown-blocks.tsx: no images, no raw
// HTML, the app's own tables and code blocks). A [S#] marker (made a "#cite-#" link
// by remarkCitations) is a citation chip.
const ANSWER_MARKDOWN: Components = {
  ...MARKDOWN_BLOCKS,
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
