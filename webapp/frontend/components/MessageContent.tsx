'use client';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChartBlock } from './ChartBlock';

const components: Components = {
  code({ node, className, children, ...props }) {
    const match = /language-([\w-]+)/.exec(className || '');
    const lang = match?.[1];
    const raw = String(children).replace(/\n$/, '');
    if (lang === 'vega-lite' || lang === 'vega') {
      return <ChartBlock spec={raw} />;
    }
    if (match) {
      return (
        <pre className="overflow-x-auto rounded-lg bg-slate-100 p-3 text-xs dark:bg-slate-800">
          <code className={className} {...props}>{children}</code>
        </pre>
      );
    }
    return (
      <code className="rounded bg-slate-100 px-1 py-0.5 text-[0.85em] dark:bg-slate-800" {...props}>
        {children}
      </code>
    );
  },
  table({ children }) {
    return (
      <div className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">{children}</table>
      </div>
    );
  },
  th({ children }) {
    return <th className="border-b border-slate-300 px-2 py-1 font-semibold dark:border-slate-600">{children}</th>;
  },
  td({ children }) {
    return <td className="border-b border-slate-200 px-2 py-1 dark:border-slate-700">{children}</td>;
  },
  a({ children, href }) {
    return <a href={href} className="text-accent underline" target="_blank" rel="noreferrer">{children}</a>;
  },
  ul({ children }) {
    return <ul className="my-1 list-disc pl-5">{children}</ul>;
  },
  ol({ children }) {
    return <ol className="my-1 list-decimal pl-5">{children}</ol>;
  },
  h1({ children }) {
    return <h1 className="mb-1 mt-2 text-base font-semibold">{children}</h1>;
  },
  h2({ children }) {
    return <h2 className="mb-1 mt-2 text-sm font-semibold">{children}</h2>;
  },
  h3({ children }) {
    return <h3 className="mb-1 mt-2 text-sm font-semibold">{children}</h3>;
  },
  p({ children }) {
    return <p className="my-1 leading-relaxed">{children}</p>;
  },
};

export function MessageContent({ text }: { text: string }) {
  // No rehype-raw: raw HTML stays inert. remark-gfm enables tables/strikethrough/task lists.
  return (
    <div className="max-w-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
