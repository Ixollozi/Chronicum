"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import rehypeHighlight from "rehype-highlight";
import type { Components } from "react-markdown";

function normalizeMd(text: string): string {
  return text.replace(/\r\n/g, "\n").trim();
}

const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  pre: ({ children }) => <pre className="rich-pre">{children}</pre>,
  code: ({ className, children, ...props }) => {
    const isBlock = Boolean(className?.includes("language-") || className?.includes("hljs"));
    if (isBlock) {
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className="rich-inline-code" {...props}>
        {children}
      </code>
    );
  },
};

export function RichText({
  text,
  className = "",
}: {
  text: string | null | undefined;
  className?: string;
}) {
  const md = useMemo(() => (text ? normalizeMd(text) : ""), [text]);
  if (!md) return null;

  return (
    <div className={`rich-text ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        rehypePlugins={[rehypeHighlight]}
        components={components}
      >
        {md}
      </ReactMarkdown>
    </div>
  );
}
