/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    fontFamily: {
      sans: ['Inter', 'system-ui', 'sans-serif'],
      mono: ['JetBrains Mono', 'Geist Mono', 'monospace'],
    },
    fontSize: {
      '2xs': ['11px', { lineHeight: '16px', fontWeight: '400' }],
      xs: ['12px', { lineHeight: '16px', fontWeight: '400' }],
      sm: ['13px', { lineHeight: '20px', fontWeight: '400' }],
      base: ['14px', { lineHeight: '20px', fontWeight: '400' }],
      md: ['16px', { lineHeight: '24px', fontWeight: '500' }],
      lg: ['18px', { lineHeight: '28px', fontWeight: '600' }],
      xl: ['24px', { lineHeight: '32px', fontWeight: '700', letterSpacing: '-0.03em' }],
      '2xl': ['32px', { lineHeight: '40px', fontWeight: '800', letterSpacing: '-0.04em' }],
    },
    borderRadius: {
      none: '0',
      sm: '3px',
      DEFAULT: '4px',
      md: '6px',
      lg: '8px',
      full: '9999px',
    },
    extend: {
      colors: {
        // Фоны
        base: '#0F1117',
        surface: '#151520',
        elevated: '#1E2030',

        // Акцент
        accent: {
          DEFAULT: '#6366F1',
          muted: 'rgba(99,102,241,0.15)',
          hover: '#818CF8',
        },

        // Семантические
        danger: {
          DEFAULT: '#EF4444',
          muted: '#2D1515',
        },
        warning: {
          DEFAULT: '#F59E0B',
          muted: '#291D0A',
        },
        success: {
          DEFAULT: '#10B981',
          muted: '#0A2118',
        },
        early: {
          DEFAULT: '#A78BFA',
          muted: '#1E1530',
        },
        neutral: '#52525B',

        // Текст
        primary: '#EEEFF1',
        secondary: '#94A3B8',
        muted: '#52525B',

        // Разделители
        border: 'rgba(255,255,255,0.08)',
        'border-hover': 'rgba(255,255,255,0.15)',
      },
      backgroundColor: {
        page: '#0F1117',
      },
      spacing: {
        xs: '4px',
        sm: '8px',
        md: '16px',
        lg: '24px',
        xl: '32px',
        '2xl': '48px',
        sidebar: '240px',
        'sidebar-collapsed': '56px',
      },
      maxWidth: {
        content: '1440px',
      },
      animation: {
        'fade-in': 'fadeIn 0.2s ease-out',
        'slide-in-right': 'slideInRight 0.25s ease-out',
        'pulse-dot': 'pulseDot 2s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideInRight: {
          from: { transform: 'translateX(100%)' },
          to: { transform: 'translateX(0)' },
        },
        pulseDot: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.5' },
        },
      },
    },
  },
  plugins: [],
};
