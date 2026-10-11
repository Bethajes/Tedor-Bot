/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Tedor brand palette, so the Mini App reads as the same product the
        // bot and the website belong to.
        tedor: {
          50: "#eef7f4",
          100: "#d6ece4",
          500: "#1f9d76",
          600: "#167f5f",
          700: "#11664c",
        },
      },
    },
  },
  plugins: [],
};
