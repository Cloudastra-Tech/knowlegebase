import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"

import { MARKDOWN_BLOCKS } from "@/lib/markdown-blocks"
import { cn } from "@/lib/utils"

// A document's own links: a web link opens in a new tab; a relative one (a path in
// the source app, e.g. Outline's /doc/...) means nothing here, so it is its text.
const PASSAGE_MARKDOWN: Components = {
  ...MARKDOWN_BLOCKS,
  a: ({ href, children }) =>
    href && /^https?:\/\//i.test(href) ? (
      <a href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    ) : (
      <>{children}</>
    ),
}

// A piece of a cited document, as the document shows it: headings, lists, bold,
// tables. Sized for the source panel, not the answer's reading measure.
export function PassageMarkdown({ text, className }: { text: string; className?: string }) {
  return (
    <div
      className={cn(
        "text-sm leading-relaxed break-words",
        "[&_a]:underline [&_a]:underline-offset-2 [&_li]:my-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5",
        "[&>:first-child]:mt-0 [&>:last-child]:mb-0 [&_h1,&_h2,&_h3,&_h4,&_h5,&_h6]:mt-4 [&_h1,&_h2,&_h3,&_h4,&_h5,&_h6]:mb-1.5 [&_h1,&_h2,&_h3,&_h4,&_h5,&_h6]:text-sm [&_h1,&_h2,&_h3,&_h4,&_h5,&_h6]:font-semibold",
        "[&_strong]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3",
        "[&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:py-0.5 [&_:not(pre)>code]:font-mono [&_:not(pre)>code]:text-[0.85em]",
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={PASSAGE_MARKDOWN}>
        {text}
      </ReactMarkdown>
    </div>
  )
}
