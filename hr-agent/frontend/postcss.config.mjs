// Tailwind v4 ships its own PostCSS plugin (`@tailwindcss/postcss`) and
// inlines the autoprefixer pipeline. Do NOT add `tailwindcss` or
// `autoprefixer` here -- v4 owns both.
export default {
  plugins: { "@tailwindcss/postcss": {} },
};
