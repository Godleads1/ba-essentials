/** Shared Tailwind setup for every page in /website. Rebuild app.css after editing any page: npm run build */
module.exports = {
  content: ['../*.html', '../*.js'],
  theme: {
    extend: {
      colors: {
        brand: { orange: '#D9662E', orangeDark: '#AD4C1D', cta: '#BE5928', ctaDark: '#A34A1F', navy: '#33397F', navySoft: '#E7E8F3', lavender: '#B8BBE6' },
        ink: { DEFAULT: '#241A15', muted: '#5C534D', soft: '#736C66' },
        cream: { DEFAULT: '#F5F0E8', light: '#FBF9F5', line: '#ECE5DA' },
        cocoa: { DEFAULT: '#3B251C', deep: '#1C1712' },
        pill: { orangeBg: '#FBE6DA', greenBg: '#DDF1EC', greenTx: '#1E7761', navyBg: '#ECEDF8' },
      },
      fontFamily: {
        display: ['Lexend', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      maxWidth: { site: '1000px' },
    },
  },
};
