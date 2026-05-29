"use client";

import * as ContextMenu from "@radix-ui/react-context-menu";
import { ExternalLink, Eye, Check, X, Copy } from "lucide-react";
import { useRouter } from "next/navigation";

interface Props {
  applicationId: string;
  candidateName: string;
  email: string | null;
  onQuickView: () => void;
  children: React.ReactNode;
}

export function CandidateContextMenu({ applicationId, candidateName, email, onQuickView, children }: Props) {
  const router = useRouter();

  function copyEmail() {
    if (email) navigator.clipboard.writeText(email);
  }

  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger asChild>{children}</ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Content className="z-50 min-w-[180px] rounded-lg border border-border bg-popover p-1.5 shadow-pop">
          <ContextMenu.Item
            className="flex cursor-pointer items-center gap-2 rounded-md px-3 py-1.5 text-sm outline-none hover:bg-accent/40"
            onSelect={() => router.push(`/candidates/${applicationId}`)}
          >
            <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
            Open detail
          </ContextMenu.Item>
          <ContextMenu.Item
            className="flex cursor-pointer items-center gap-2 rounded-md px-3 py-1.5 text-sm outline-none hover:bg-accent/40"
            onSelect={onQuickView}
          >
            <Eye className="h-3.5 w-3.5 text-muted-foreground" />
            Quick view
          </ContextMenu.Item>
          {email && (
            <ContextMenu.Item
              className="flex cursor-pointer items-center gap-2 rounded-md px-3 py-1.5 text-sm outline-none hover:bg-accent/40"
              onSelect={copyEmail}
            >
              <Copy className="h-3.5 w-3.5 text-muted-foreground" />
              Copy email
            </ContextMenu.Item>
          )}
          <ContextMenu.Separator className="my-1 h-px bg-border" />
          <ContextMenu.Label className="px-3 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
            {candidateName || "Unnamed"}
          </ContextMenu.Label>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}
