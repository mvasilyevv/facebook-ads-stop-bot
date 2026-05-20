/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    fontFamily: {
      sans: ['Inter', 'system-ui', 'sans-serif'],
      mono: ['JetBrains Mono', 'monospace'],
      display: ['Inter', 'system-ui', 'sans-serif'],
    },
    fontSize: {
      '2xs': ['11px', { lineHeight: '16px', fontWeight: '400' }],
      xs: ['12px', { lineHeight: '16px', fontWeight: '400' }],
      sm: ['13px', { lineHeight: '20px', fontWeight: '400' }],
      base: ['14px', { lineHeight: '20px', fontWeight: '400' }],
      md: ['16px', { lineHeight: '24px', fontWeight: '500' }],
      lg: ['18px', { lineHeight: '28px', fontWeight: '600' }],
      xl: ['24px', { lineHeight: '32px', fontWeight: '600', letterSpacing: '-0.02em' }],
      '2xl': ['32px', { lineHeight: '40px', fontWeight: '600', letterSpacing: '-0.03em' }],
    },
    borderRadius: {
      none: '0',
      sm: '3px',
      DEFAULT: '4px',
      md: '6px',
      lg: '8px',
      xl: '12px',
      full: '9999px',
    },
    extend: {
      colors: {
        bg: '#0E1116',
        base: '#0E1116',
        surface: '#14181E',
        'surface-2': '#1A1F26',
        elevated: '#1A1F26',
        border: '#232A33',
        'border-hover': 'rgba(255, 255, 255, 0.15)',
        text: '#E8EBEE',
        'text-dim': '#8A929D',
        'text-muted': '#5A6270',
        primary: '#E8EBEE',
        secondary: '#8A929D',
        muted: '#5A6270',
        accent: {
          DEFAULT: '#FF6B00',
          soft: 'rgba(255, 107, 0, 0.13)',
          muted: 'rgba(255, 107, 0, 0.13)',
          hover: '#FF7A00',
        },
        danger: {
          DEFAULT: '#FF3B3B',
          muted: 'rgba(255, 59, 59, 0.12)',
        },
        warning: {
          DEFAULT: '#FFB020',
          muted: 'rgba(255, 176, 32, 0.12)',
        },
        success: {
          DEFAULT: '#5AFF6A',
          muted: 'rgba(90, 255, 106, 0.12)',
        },
        early: {
          DEFAULT: '#5CE6FF',
          muted: 'rgba(92, 230, 255, 0.12)',
        },
        neutral: '#5A6270',
        ok: '#5AFF6A',
        warn: '#FFB020',
        stop: '#FF3B3B',
        info: '#5CE6FF',
      },
      backgroundColor: {
        page: 'var(--bg)',
      },
      boxShadow: {
        panel: '0 1px 0 rgba(255, 107, 0, 0.04)',
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
        'fade-in': 'fadeIn 0.25s ease-out forwards',
        'slide-in-right': 'slideInRight 0.25s ease-out',
        'pulse-dot': 'pulseDot 2s ease-in-out infinite',
        'scan-pulse': 'scanPulse 1.8s ease-in-out infinite',
        'stagger-in': 'staggerIn 0.4s ease-out forwards',
        'incident-enter': 'incidentEnter 0.32s ease-out forwards',
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
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.45', transform: 'scale(0.85)' },
        },
        scanPulse: {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(255, 107, 0, 0)' },
          '50%': { boxShadow: '0 0 0 2px rgba(255, 107, 0, 0.12)' },
        },
        staggerIn: {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        incidentEnter: {
          from: { opacity: '0', transform: 'translateX(-4px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
      },
    },
  },
  plugins: [],
};
