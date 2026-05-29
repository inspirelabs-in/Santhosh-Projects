"use client";

import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

interface Props {
  value: string;
  configured: boolean;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}

export function SecretInput({ value, configured, onChange, placeholder, disabled }: Props) {
  const [editing, setEditing] = useState(!configured);
  const [show, setShow] = useState(false);

  if (configured && !editing) {
    return (
      <div className="flex gap-2">
        <Input value="••••••••••••" disabled className="font-mono" />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            setEditing(true);
            onChange("");
          }}
          disabled={disabled}
        >
          Replace
        </Button>
      </div>
    );
  }

  return (
    <div className="flex gap-2">
      <Input
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? "paste new value"}
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
        className="font-mono"
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Hide" : "Show"}
      >
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </Button>
    </div>
  );
}
