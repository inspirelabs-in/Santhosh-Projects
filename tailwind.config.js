/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./app/templates/**/*.html'],
  theme: {
    extend: {
      fontFamily: { outfit: ['Inter', 'system-ui', 'sans-serif'] },
      colors: {
        slate: {
          950: '#0a0f1d',
          925: '#0d1321',
        },
      },
    },
  },
  plugins: [],
}
