/** The design preview's Tailwind setup (same theme as static/banda/app.css), used to build
 *  static/banda/staff.css for the staff pages. Rebuild after editing them: npm run build */
module.exports = {
  content: ['../../templates/banda/staff/**/*.html', '../../apps/core/client_status.py'],
  corePlugins: { preflight: false },
  // Scope utilities to the staff content so they never override the shell's own classes.
  important: '.staff',
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
