import type { Config } from "tailwindcss";
// tailwindcss-animate is Tailwind v3 plugin format; not loaded under v4.
// The accordion-down/up keyframes we actually use are defined inline below.

/**
 * GrabOn Hiring Tailwind config.
 * Tokens live in `src/app/globals.css` as HSL CSS variables. Add a new color
 * here only if it needs a Tailwind class (e.g. `bg-brand-green`).
 */
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1.5rem", screens: { "2xl": "1440px" } },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        success: { DEFAULT: "hsl(var(--success))", foreground: "hsl(var(--success-foreground))" },
        warning: { DEFAULT: "hsl(var(--warning))", foreground: "hsl(var(--warning-foreground))" },
        info: { DEFAULT: "hsl(var(--info))", foreground: "hsl(var(--info-foreground))" },
        // Direct brand swatches -- prefer semantic tokens above for app UI.
        brand: {
          green: "hsl(var(--brand-green))",
          "green-light": "hsl(var(--brand-green-light))",
          "blue-deep": "hsl(var(--brand-blue-deep))",
          blue: "hsl(var(--brand-blue))",
          "blue-dark": "hsl(var(--brand-blue-dark))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "calc(var(--radius) + 4px)",
        "2xl": "calc(var(--radius) + 10px)",
      },
      fontFamily: {
        // Brand mandate: single family, Nunito Sans, everywhere.
        // ``mono`` aliases the same family so any `font-mono` Tailwind
        // utility renders Nunito with tabular-nums (handled in globals.css).
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        data: ["var(--font-data)", "'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      boxShadow: {
        // Soft, brand-tinted elevation. Used by Card + Dialog by default.
        card: "0 1px 2px hsl(var(--brand-blue-deep) / 0.04), 0 4px 16px -4px hsl(var(--brand-blue-deep) / 0.06)",
        pop: "0 8px 28px -8px hsl(var(--brand-blue-deep) / 0.18), 0 2px 4px hsl(var(--brand-blue-deep) / 0.06)",
        focus: "0 0 0 3px hsl(var(--primary) / 0.25)",
      },
      keyframes: {
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
        "accordion-up": { from: { height: "var(--radix-accordion-content-height)" }, to: { height: "0" } },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [],
};
export default config;
