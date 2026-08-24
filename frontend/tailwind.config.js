/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0A0D12",
          900: "#0F141B",
          800: "#161C25",
          700: "#1E2632",
          600: "#2A3441",
          500: "#3C4A5C",
        },
        mist: {
          400: "#5C6B7F",
          300: "#8A97A8",
          200: "#B7C0CC",
          100: "#E4E8EC",
        },
        amber: {
          DEFAULT: "#E8A33D",
          400: "#F2B65C",
          500: "#E8A33D",
          600: "#C6822A",
        },
        teal: {
          DEFAULT: "#3FD1C6",
          400: "#5EDCD2",
          500: "#3FD1C6",
          600: "#28A89E",
        },
        rose: {
          DEFAULT: "#E86A6A",
          500: "#E86A6A",
          600: "#C94F4F",
        },
      },
      fontFamily: {
        display: ["'Space Grotesk'", "sans-serif"],
        body: ["'Inter'", "sans-serif"],
        mono: ["'IBM Plex Mono'", "monospace"],
      },
      backgroundImage: {
        "scan-grid":
          "linear-gradient(rgba(63,209,198,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(63,209,198,0.06) 1px, transparent 1px)",
      },
      keyframes: {
        scan: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100%)" },
        },
        pulseSoft: {
          "0%, 100%": { opacity: 1 },
          "50%": { opacity: 0.5 },
        },
      },
      animation: {
        scan: "scan 2.4s linear infinite",
        pulseSoft: "pulseSoft 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
