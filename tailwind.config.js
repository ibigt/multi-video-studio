/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/static/index.html',
    './app/static/js/**/*.js'
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#070b14',
          900: '#0b1120',
          850: '#0e1628',
          800: '#131d33',
          700: '#1c2a45'
        },
        brand: {
          300: '#8fb4ff',
          400: '#6592ff',
          500: '#3b76f6',
          600: '#285fdc'
        }
      },
      boxShadow: {
        panel: '0 22px 60px -36px rgba(0, 0, 0, 0.9)',
        glow: '0 0 0 1px rgba(101, 146, 255, 0.22), 0 16px 50px -24px rgba(59, 118, 246, 0.55)'
      },
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'system-ui', 'sans-serif'],
        mono: ['Cascadia Code', 'SFMono-Regular', 'Consolas', 'monospace']
      },
      keyframes: {
        pulseSoft: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '.35' }
        },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' }
        }
      },
      animation: {
        'pulse-soft': 'pulseSoft 1.6s ease-in-out infinite',
        'slide-up': 'slideUp .22s ease-out both'
      }
    }
  },
  plugins: []
}
