/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: [
    './pages/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './app/**/*.{ts,tsx}',
    './src/**/*.{ts,tsx}',
    './node_modules/streamdown/dist/**/*.{js,mjs}',
  ],
  safelist: ['w-6', 'w-7', 'w-8', 'w-9', 'w-10', 'w-11', 'w-12'],
  theme: {
    extend: {
      screens: {
        desktop: '936px',
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif', 'system-ui'],
        mono: ['JetBrains Mono', 'monospace'],
        'dm-sans': ['DM Sans', 'sans-serif'],
        'kumbh-sans': ['Kumbh Sans', 'sans-serif'],
      },
      colors: {
        'sq-bg-dark': '#191A1A',
        // ??? lol that's what its called in Figma!
        'sq-background-light': '#F1F1F1',
        'sq-bg-secondary-dark': '#212121',
        'sq-bg-light': '#E5E5E3',
        'sq-bg-secondary-light': '#ECECEB',
        'sq-blue': '#00A6FF',
        'sq-blue-dark': '#00A6FF',
        'sq-text-primary': '#E5E5E5',
        'sq-text-secondary': '#949494',
        'sq-text-tertiary': '#676767',
        'secondary-tan': '#E5E5E3',
        'background-color': '#191A1A',
        'sq-neutral-100': '#D7D7D7',
        'sq-neutral-200': '#ADADAD',
        'sq-neutral-700': '#3B3B3B',
        'sq-neutral-900': '#171818',
        'white-16%': 'rgba(255,255,255,0.16)',
        'white-700': '#D7D7D7',
        'white-500': '#949494',
        'sq-background-1': '#212121',
        'sq-background-2': '#191A1A',
        'sq-neutral-950': '#111111',
        'sq-neutral-900': '#171818',
        'sq-neutral-800': '#2D2D2D',
        'sq-neutral-700': '#3B3B3B',
        'sq-neutral-500': '#5A5A5A',
        'sq-neutral-400': '#676767',
        'sq-neutral-300': '#949494',
        'sq-neutral-200': '#ADADAD',
        'sq-neutral-100': '#D7D7D7',
        'sq-neutral-50': '#E5E5E5',
        'sq-neutral-10': '#F2F2F2',
        'sq-neutral-0': '#F6F6F6',
        pink: '#5EBFFF',
        'sidebar-color': '#212121',
        'bg-gray': 'rgba(29, 29, 29)',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        chart: {
          1: 'hsl(var(--chart-1))',
          2: 'hsl(var(--chart-2))',
          3: 'hsl(var(--chart-3))',
          4: 'hsl(var(--chart-4))',
          5: 'hsl(var(--chart-5))',
        },
      },
      keyframes: {
        'accordion-down': {
          from: {
            height: '0',
          },
          to: {
            height: 'var(--radix-accordion-content-height)',
          },
        },
        'accordion-up': {
          from: {
            height: 'var(--radix-accordion-content-height)',
          },
          to: {
            height: '0',
          },
        },
        'dot-bounce-1': {
          '0%, 80%, 100%': { transform: 'translateY(0)' },
          '40%': { transform: 'translateY(-8px)' },
        },
        'dot-bounce-2': {
          '0%, 20%, 100%': { transform: 'translateY(0)' },
          '60%': { transform: 'translateY(-8px)' },
        },
        'dot-bounce-3': {
          '0%, 40%, 100%': { transform: 'translateY(0)' },
          '80%': { transform: 'translateY(-8px)' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        'dot-bounce-1': 'dot-bounce-1 1.0s infinite ease-in-out',
        'dot-bounce-2': 'dot-bounce-2 1.0s infinite ease-in-out',
        'dot-bounce-3': 'dot-bounce-3 1.0s infinite ease-in-out',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};
