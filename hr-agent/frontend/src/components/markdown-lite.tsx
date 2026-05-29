"use client";

import { Fragment, ReactNode } from "react";

function renderInline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const regex = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)|(https?:\/\/[^\s)]+))/g;
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) parts.push(<Fragment key={key++}>{text.slice(last, m.index)}</Fragment>);
    if (m[2] !== undefined) {
      parts.push(<strong key={key++} className="font-semibold text-foreground">{m[2]}</strong>);
    } else if (m[3] !== undefined) {
      parts.push(
        <code key={key++} className="rounded bg-muted px-1 py-0.5 font-mono text-[12px] text-foreground">
          {m[3]}
        </code>,
      );
    } else if (m[4] !== undefined && m[5] !== undefined) {
      parts.push(
        <a
          key={key++}
          href={m[5]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline-offset-2 hover:underline"
        >
          {m[4]}
        </a>,
      );
    } else if (m[6] !== undefined) {
      parts.push(
        <a
          key={key++}
          href={m[6]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline-offset-2 hover:underline break-all"
        >
          {m[6]}
        </a>,
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(<Fragment key={key++}>{text.slice(last)}</Fragment>);
  return parts;
}

export function MarkdownLite({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const nodes: ReactNode[] = [];
  let listBuf: string[] = [];
  let paraBuf: string[] = [];
  let key = 0;

  const flushPara = () => {
    if (paraBuf.length === 0) return;
    nodes.push(
      <p key={key++} className="my-2 text-sm leading-relaxed text-foreground">
        {renderInline(paraBuf.join(" "))}
      </p>,
    );
    paraBuf = [];
  };
  const flushList = () => {
    if (listBuf.length === 0) return;
    nodes.push(
      <ul key={key++} className="my-2 ml-1 space-y-1.5 text-sm leading-relaxed text-foreground">
        {listBuf.map((item, i) => (
          <li key={i} className="flex gap-2">
            <span className="mt-2 inline-block h-1 w-1 shrink-0 rounded-full bg-muted-foreground/60" />
            <span>{renderInline(item)}</span>
          </li>
        ))}
      </ul>,
    );
    listBuf = [];
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushPara();
      flushList();
      const level = h[1].length;
      const sizeClass =
        level <= 2 ? "text-base font-bold" : level === 3 ? "text-sm font-semibold" : "text-sm font-medium";
      nodes.push(
        <h4 key={key++} className={`mt-5 mb-1 tracking-tight text-foreground ${sizeClass}`}>
          {renderInline(h[2])}
        </h4>,
      );
      continue;
    }
    if (/^[-*]{3,}\s*$/.test(line)) {
      flushPara();
      flushList();
      nodes.push(<hr key={key++} className="my-3 border-border/60" />);
      continue;
    }
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) {
      flushPara();
      listBuf.push(li[1]);
      continue;
    }
    flushList();
    paraBuf.push(line.trim());
  }
  flushPara();
  flushList();

  return <div className="space-y-1">{nodes}</div>;
}
