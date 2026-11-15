/* B & A Essentials: plan prices by region, shared by the landing page and sign-up.
 *
 * Region follows the country the business is based in:
 *   - Nigeria: naira, paid with Paystack.
 *   - Elsewhere in Africa: the naira price plus ₦100, in US dollars at the live rate, paid with Flutterwave.
 *   - Outside Africa: the dollar list price, paid with Flutterwave.
 * The yearly discount and the extra legal review fee are set by the super admin in the
 * live app; the values here are examples. So is the exchange rate: the app uses the live rate.
 */
(function () {
  'use strict';
  const PLANS = ['starter', 'growth', 'dedicated'];
  const NGN = { starter: 65000, growth: 97500, dedicated: 260000 };
  const USD = { starter: 55, growth: 89, dedicated: 249 };
  const ADMIN = { yearlyDiscountPct: 15, extraLegal: { ngn: 25000, usd: 40 }, africaSurchargeNgn: 100 };
  const EXAMPLE_RATE = 1550; // naira per US dollar (example; the app uses the live rate)

  // [ISO code, name, dial code, in Africa]
  const COUNTRIES = [
    ['NG', 'Nigeria', '234', 1],
    ['DZ', 'Algeria', '213', 1], ['AO', 'Angola', '244', 1], ['BJ', 'Benin', '229', 1], ['BW', 'Botswana', '267', 1],
    ['BF', 'Burkina Faso', '226', 1], ['BI', 'Burundi', '257', 1], ['CV', 'Cabo Verde', '238', 1], ['CM', 'Cameroon', '237', 1],
    ['CF', 'Central African Republic', '236', 1], ['TD', 'Chad', '235', 1], ['KM', 'Comoros', '269', 1], ['CG', 'Congo', '242', 1],
    ['CD', 'DR Congo', '243', 1], ['CI', 'Côte d’Ivoire', '225', 1], ['DJ', 'Djibouti', '253', 1], ['EG', 'Egypt', '20', 1],
    ['GQ', 'Equatorial Guinea', '240', 1], ['ER', 'Eritrea', '291', 1], ['SZ', 'Eswatini', '268', 1], ['ET', 'Ethiopia', '251', 1],
    ['GA', 'Gabon', '241', 1], ['GM', 'Gambia', '220', 1], ['GH', 'Ghana', '233', 1], ['GN', 'Guinea', '224', 1],
    ['GW', 'Guinea-Bissau', '245', 1], ['KE', 'Kenya', '254', 1], ['LS', 'Lesotho', '266', 1], ['LR', 'Liberia', '231', 1],
    ['LY', 'Libya', '218', 1], ['MG', 'Madagascar', '261', 1], ['MW', 'Malawi', '265', 1], ['ML', 'Mali', '223', 1],
    ['MR', 'Mauritania', '222', 1], ['MU', 'Mauritius', '230', 1], ['MA', 'Morocco', '212', 1], ['MZ', 'Mozambique', '258', 1],
    ['NA', 'Namibia', '264', 1], ['NE', 'Niger', '227', 1], ['RW', 'Rwanda', '250', 1], ['ST', 'São Tomé and Príncipe', '239', 1],
    ['SN', 'Senegal', '221', 1], ['SC', 'Seychelles', '248', 1], ['SL', 'Sierra Leone', '232', 1], ['SO', 'Somalia', '252', 1],
    ['ZA', 'South Africa', '27', 1], ['SS', 'South Sudan', '211', 1], ['SD', 'Sudan', '249', 1], ['TZ', 'Tanzania', '255', 1],
    ['TG', 'Togo', '228', 1], ['TN', 'Tunisia', '216', 1], ['UG', 'Uganda', '256', 1], ['ZM', 'Zambia', '260', 1], ['ZW', 'Zimbabwe', '263', 1],
    ['US', 'United States', '1', 0], ['GB', 'United Kingdom', '44', 0], ['CA', 'Canada', '1', 0], ['IE', 'Ireland', '353', 0],
    ['DE', 'Germany', '49', 0], ['FR', 'France', '33', 0], ['NL', 'Netherlands', '31', 0], ['BE', 'Belgium', '32', 0],
    ['ES', 'Spain', '34', 0], ['IT', 'Italy', '39', 0], ['PT', 'Portugal', '351', 0], ['CH', 'Switzerland', '41', 0],
    ['SE', 'Sweden', '46', 0], ['NO', 'Norway', '47', 0], ['DK', 'Denmark', '45', 0], ['FI', 'Finland', '358', 0],
    ['PL', 'Poland', '48', 0], ['AE', 'United Arab Emirates', '971', 0], ['SA', 'Saudi Arabia', '966', 0], ['QA', 'Qatar', '974', 0],
    ['IN', 'India', '91', 0], ['CN', 'China', '86', 0], ['SG', 'Singapore', '65', 0], ['MY', 'Malaysia', '60', 0],
    ['JP', 'Japan', '81', 0], ['AU', 'Australia', '61', 0], ['NZ', 'New Zealand', '64', 0], ['BR', 'Brazil', '55', 0],
    ['MX', 'Mexico', '52', 0], ['JM', 'Jamaica', '1', 0], ['TT', 'Trinidad and Tobago', '1', 0], ['TR', 'Türkiye', '90', 0],
  ];
  const byCode = Object.fromEntries(COUNTRIES.map(c => [c[0], { code: c[0], name: c[1], dial: c[2], africa: !!c[3] }]));

  // 'ng' | 'africa' | 'intl'
  function regionFor(code) {
    const c = byCode[code];
    if (!c) return null;
    if (c.code === 'NG') return 'ng';
    return c.africa ? 'africa' : 'intl';
  }

  // Monthly price in the region's currency. Rest of Africa: (naira price + ₦100) at the rate, in dollars.
  function monthly(plan, region, rate = EXAMPLE_RATE) {
    if (region === 'ng') return NGN[plan];
    if (region === 'africa') return Math.ceil(((NGN[plan] + ADMIN.africaSurchargeNgn) / rate) * 100) / 100;
    return USD[plan];
  }
  function price(plan, region, billing, rate) {
    const m = monthly(plan, region, rate);
    if (billing !== 'yearly') return { perMonth: m, total: m, every: 'month' };
    // Whole naira and whole dollars, except the rest of Africa, whose converted price keeps cents.
    const cents = region === 'africa' ? 100 : 1;
    const total = Math.round(m * 12 * (1 - ADMIN.yearlyDiscountPct / 100) * cents) / cents;
    return { perMonth: Math.round((total / 12) * cents) / cents, total, every: 'year' };
  }
  const currency = region => (region === 'ng' ? 'NGN' : 'USD');
  const provider = region => (region === 'ng' ? 'paystack' : 'flutterwave');
  function money(amount, region) {
    if (region === 'ng') return '₦' + Math.round(amount).toLocaleString('en-NG');
    const whole = Math.round(amount * 100) % 100 === 0;
    return '$' + amount.toLocaleString('en-US', { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 });
  }

  window.BANDA_PRICING = {
    PLANS, NGN, USD, ADMIN, EXAMPLE_RATE, COUNTRIES, byCode, regionFor, monthly, price, currency, provider, money,
  };
})();
