import Image from "next/image";
import { cn } from "@/lib/utils";

/**
 * GrabOn logo + monogram component.
 *
 * Brand rules (https://www.grabon.in/branding/):
 *   - Use the wordmark above ~150px width; below that, use the 'O' icon.
 *   - Three colour variants: light bg, dark bg, brand-green bg.
 *   - Never recolour, rotate, surround, or overlay the logo.
 *
 * `variant` controls which artwork file we load; `kind` chooses wordmark
 * vs icon. The component picks the right file automatically when `width`
 * is provided and below 150px (defaults to icon).
 */

type LogoBackground = "light" | "dark" | "green";
type LogoKind = "wordmark" | "icon" | "auto";

interface LogoProps {
  variant?: LogoBackground;
  kind?: LogoKind;
  width?: number;
  height?: number;
  className?: string;
  priority?: boolean;
  alt?: string;
}

const WORDMARK_DIMENSIONS = { w: 200, h: 56 };
const ICON_DIMENSIONS = { w: 64, h: 64 };

const WORDMARK_FILES: Record<LogoBackground, string> = {
  light: "/brand/logo/logo-on-light.svg",
  dark: "/brand/logo/logo-on-dark.svg",
  green: "/brand/logo/logo-on-green.svg",
};

const ICON_FILES: Record<LogoBackground, string> = {
  light: "/brand/icon/icon-on-light.svg",
  dark: "/brand/icon/icon-on-dark.svg",
  green: "/brand/icon/icon-on-green.svg",
};

export function Logo({
  variant = "light",
  kind = "auto",
  width,
  height,
  className,
  priority = false,
  alt = "GrabOn",
}: LogoProps) {
  const useIcon = kind === "icon" || (kind === "auto" && (width ?? Infinity) < 150);
  const src = useIcon ? ICON_FILES[variant] : WORDMARK_FILES[variant];
  const fallbackDims = useIcon ? ICON_DIMENSIONS : WORDMARK_DIMENSIONS;

  return (
    <Image
      src={src}
      alt={alt}
      width={width ?? fallbackDims.w}
      height={height ?? fallbackDims.h}
      priority={priority}
      className={cn("select-none", className)}
    />
  );
}
