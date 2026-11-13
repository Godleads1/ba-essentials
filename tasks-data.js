/* Sample task data shared by My Tasks (my-tasks.html) and Request Detail (task.html).
   Design preview only: the Django app will serve real tasks from its API.
   Changes a viewer makes (cancelling, sending a message) are kept in this browser tab
   with sessionStorage so the preview flows between pages. */
(function () {
  const CATS = {
    business: { name: 'Business & Admin', icon: 'briefcase', tone: 'bg-pill-navyBg text-brand-navy' },
    legal: { name: 'Legal & IP Review', icon: 'scale', tone: 'bg-pill-orangeBg text-brand-orangeDark' },
    ops: { name: 'Operations & Logistics', icon: 'truck', tone: 'bg-pill-greenBg text-pill-greenTx' },
    creator: { name: 'Creator Essentials', icon: 'camera', tone: 'bg-[#EFE9FB] text-[#5B3FA8]' },
    tax: { name: 'Tax Readiness', icon: 'calculator', tone: 'bg-cream text-ink' },
    addons: { name: 'Specialized Add-Ons', icon: 'sparkles', tone: 'bg-pill-navyBg text-brand-navy' },
  };

  // Order here is the order of the status guide.
  const STATUS = {
    submitted: { label: 'Submitted', chip: 'bg-cream text-ink', dot: 'bg-ink-soft', about: 'Received and waiting in the queue' },
    scoping: { label: 'Being scoped', chip: 'border border-brand-navy/40 text-brand-navy', dot: 'bg-brand-lavender', about: 'We’re confirming what’s needed and any cost' },
    progress: { label: 'In progress', chip: 'bg-pill-navyBg text-brand-navy', dot: 'bg-brand-navy', about: 'A specialist is working on it' },
    input: { label: 'Needs your info', chip: 'bg-pill-orangeBg text-brand-orangeDark', dot: 'bg-brand-orange', about: 'We need something from you to continue' },
    checking: { label: 'Being checked', chip: 'bg-[#EFE9FB] text-[#5B3FA8]', dot: 'bg-[#7C5CC4]', about: 'Quality check before it reaches you' },
    hold: { label: 'On hold', chip: 'bg-[#F1EFEC] text-ink-muted', dot: 'bg-[#B5ADA5]', about: 'Paused for now, usually at your request' },
    delivered: { label: 'Delivered', chip: 'bg-pill-greenBg text-pill-greenTx', dot: 'bg-pill-greenTx', about: 'Finished and sent to you' },
    cancelled: { label: 'Cancelled', chip: 'bg-[#FDECEC] text-[#B42318]', dot: 'bg-[#B42318]', about: 'Stopped before work began' },
  };
  // A request can be cancelled until a specialist starts on it.
  const CANCELLABLE = ['submitted', 'scoping'];

  const PRIORITY = {
    High: 'bg-pill-orangeBg text-brand-orangeDark',
    Medium: 'bg-pill-navyBg text-brand-navy',
    Low: 'bg-cream text-ink-muted',
  };

  const TEAM = 'B & A team';
  const TASKS = [
    { id: 'REQ-2026-0048', title: 'Business proposal document', cat: 'business', service: 'Proposal review and editing', status: 'progress', priority: 'Medium',
      submitted: '2026-11-06T10:24', due: '2026-11-13',
      details: 'Review and tighten our proposal for the Lagos State catering tender. Keep it under 12 pages and make the pricing section clearer.',
      files: [['Catering_Tender_Proposal_v3.docx', 1840000], ['Pricing_Sheet.xlsx', 96000]],
      messages: [[TEAM, '2026-11-07T09:10', 'Thanks, Chinedu. We’ve started on the proposal and will send a first draft by Wednesday.']] },
    { id: 'REQ-2026-0047', title: 'Social media content calendar', cat: 'addons', service: 'Social media management', status: 'input', priority: 'High',
      submitted: '2026-11-05T15:42', due: '2026-11-15',
      details: 'A 3-month content calendar for Instagram and TikTok for our new snack range, with post ideas and captions.',
      files: [['Brand_Guidelines.pdf', 2310000]],
      quote: { amount: 120000, state: 'Awaiting payment' },
      messages: [
        [TEAM, '2026-11-06T11:20', 'We’ve sent a quote for this add-on. Could you also tell us the tone you prefer for captions: playful, or more polished?'],
        ['You', '2026-11-06T13:05', 'Playful, but no slang. I’ll check the quote today.'],
        [TEAM, '2026-11-07T10:02', 'Great. Once the quote is paid we’ll start. Can you share 3 accounts you like the style of?'],
      ] },
    { id: 'REQ-2026-0046', title: 'Trademark filing support', cat: 'legal', service: 'Trademark application', status: 'scoping', priority: 'High',
      submitted: '2026-11-04T09:10', due: '2026-11-16',
      details: 'File a trademark for our brand name “Okeke Foods” and the logo, in the class for packaged snacks.',
      files: [['Okeke_Foods_Logo.png', 412000]], messages: [] },
    { id: 'REQ-2026-0045', title: 'Monthly bookkeeping', cat: 'addons', service: 'Bookkeeping support', status: 'delivered', priority: 'Medium',
      submitted: '2026-10-28T11:12', due: '2026-11-05',
      details: 'Record October sales and expenses from the attached statements and receipts.',
      files: [['October_Statement.pdf', 880000], ['Receipts_October.pdf', 3400000]],
      messages: [[TEAM, '2026-11-04T16:40', 'Your October books are ready. The summary shows a few uncategorised expenses we flagged for you.']] },
    { id: 'REQ-2026-0044', title: 'Vendor coordination', cat: 'ops', service: 'Schedule and brief a vendor', status: 'input', priority: 'Medium',
      submitted: '2026-11-03T16:36', due: '2026-11-18',
      details: 'Brief our new packaging supplier on the December order and agree a delivery date.',
      files: [],
      messages: [[TEAM, '2026-11-05T12:15', 'Could you share the supplier’s contact person and the order quantities for December?']] },
    { id: 'REQ-2026-0043', title: 'Company registration', cat: 'legal', service: 'CAC limited company', status: 'progress', priority: 'High',
      submitted: '2026-11-02T14:07', due: '2026-11-20',
      details: 'Register Okeke Foods as a limited company. Two directors; documents attached.',
      files: [['Director_IDs.pdf', 1200000], ['Proposed_Names.docx', 42000]],
      messages: [[TEAM, '2026-11-04T10:30', 'Name search approved. We’re preparing the incorporation documents.']] },
    { id: 'REQ-2026-0042', title: 'Inventory tracker spreadsheet', cat: 'business', service: 'CRM or spreadsheet updates', status: 'delivered', priority: 'Low',
      submitted: '2026-10-27T13:05', due: '2026-11-03',
      details: 'Clean up our inventory sheet and add low-stock alerts.',
      files: [['Inventory_2026.xlsx', 230000]], messages: [] },
    { id: 'REQ-2026-0041', title: 'NDA review', cat: 'legal', service: 'NDA', status: 'delivered', priority: 'Medium',
      submitted: '2026-10-22T09:45', due: '2026-10-30',
      details: 'Review the NDA from a distributor before we share our recipes.',
      files: [['Distributor_NDA.pdf', 320000]], messages: [] },
    { id: 'REQ-2026-0040', title: 'Q3 income summary', cat: 'tax', service: 'Income summary', status: 'checking', priority: 'Medium',
      submitted: '2026-11-01T12:20', due: '2026-11-14',
      details: 'Compile July to September income from our invoices for our accountant.',
      files: [['Q3_Invoices.zip', 5600000]], messages: [] },
    { id: 'REQ-2026-0039', title: 'Abuja trip: flights and hotel', cat: 'ops', service: 'Flight and hotel booking', status: 'delivered', priority: 'High',
      submitted: '2026-10-20T08:30', due: '2026-10-28',
      details: 'Return flights Lagos–Abuja for 2 people, 27–29 Oct, and a hotel near the Wuse 2 office.',
      files: [], messages: [] },
    { id: 'REQ-2026-0038', title: 'Brand deal contract filing', cat: 'creator', service: 'File brand deal contracts', status: 'submitted', priority: 'Low',
      submitted: '2026-11-11T17:50', due: '2026-11-22',
      details: 'File the signed brand deal contracts from this quarter and set up a folder for future ones.',
      files: [['Signed_Contracts.zip', 4100000]], messages: [] },
    { id: 'REQ-2026-0037', title: 'Monthly invoices', cat: 'business', service: 'Create and send invoices', status: 'hold', priority: 'Medium',
      submitted: '2026-10-30T10:00', due: '2026-11-25',
      details: 'Create and send November invoices to our 14 retail customers.',
      files: [['Customer_List.xlsx', 54000]],
      messages: [['You', '2026-11-02T09:15', 'Please pause this until our new prices are final.'], [TEAM, '2026-11-02T10:01', 'Done. We’ve put it on hold. Message us when you’re ready.']] },
  ];

  // ---------- deliverables: finished (or nearly finished) files from the team ----------
  const DSTATUS = {
    review: { label: 'Being checked', chip: 'bg-[#EFE9FB] text-[#5B3FA8]', about: 'Quality check before it’s released to you' },
    approval: { label: 'Needs your approval', chip: 'bg-pill-orangeBg text-brand-orangeDark', about: 'A draft for you to approve or ask changes on' },
    ready: { label: 'Ready to download', chip: 'bg-pill-greenBg text-pill-greenTx', about: 'New, and not downloaded yet' },
    downloaded: { label: 'Downloaded', chip: 'bg-pill-navyBg text-brand-navy', about: 'You’ve downloaded it at least once' },
    archived: { label: 'Archived', chip: 'bg-[#F1EFEC] text-ink-muted', about: 'Moved out of the main list' },
  };
  const DOWNLOADABLE = ['ready', 'downloaded', 'archived'];
  // Each delivered task includes one consolidated revision request within 2 days of delivery (Terms 6.2).
  const REVISION_NOTE = 'Each delivered task includes one round of changes, asked for within 2 days of delivery and within the agreed scope.';
  const DELIVERABLES = [
    { id: 'D-0109', name: 'Q3_Income_Summary.xlsx', size: 188000, task: 'REQ-2026-0040', status: 'review', at: '2026-11-12T09:05', reviewer: 'B & A quality check' },
    { id: 'D-0108', name: 'Proposal_Draft_v1.docx', size: 412000, task: 'REQ-2026-0048', status: 'approval', at: '2026-11-11T16:20', reviewer: 'B & A quality check' },
    { id: 'D-0107', name: 'October_Summary.pdf', size: 210000, task: 'REQ-2026-0045', status: 'ready', at: '2026-11-04T16:40', reviewer: 'B & A quality check' },
    { id: 'D-0106', name: 'October_Books.xlsx', size: 154000, task: 'REQ-2026-0045', status: 'downloaded', at: '2026-11-04T16:40', reviewer: 'B & A quality check' },
    { id: 'D-0105', name: 'CAC_Name_Search_Result.pdf', size: 96000, task: 'REQ-2026-0043', status: 'ready', at: '2026-11-04T10:30', reviewer: 'Mr Bamgbose, accredited filing agent' },
    { id: 'D-0104', name: 'Inventory_Tracker.xlsx', size: 260000, task: 'REQ-2026-0042', status: 'downloaded', at: '2026-11-02T12:10', reviewer: 'B & A quality check' },
    { id: 'D-0103', name: 'NDA_Review_Notes.pdf', size: 180000, task: 'REQ-2026-0041', status: 'ready', at: '2026-10-29T15:00', reviewer: 'Mr Ayodeji, legal reviewer' },
    { id: 'D-0102', name: 'Abuja_Itinerary.pdf', size: 140000, task: 'REQ-2026-0039', status: 'archived', at: '2026-10-26T09:30', reviewer: 'B & A quality check' },
    { id: 'D-0101', name: 'Abuja_Hotel_Booking.pdf', size: 122000, task: 'REQ-2026-0039', status: 'archived', at: '2026-10-26T09:30', reviewer: 'B & A quality check' },
  ];
  const fileType = name => {
    const ext = (name.split('.').pop() || '').toLowerCase();
    if (['xls', 'xlsx', 'csv'].includes(ext)) return { label: 'Spreadsheet', icon: 'file-spreadsheet', tone: 'bg-pill-greenBg text-pill-greenTx' };
    if (['png', 'jpg', 'jpeg'].includes(ext)) return { label: 'Image', icon: 'image', tone: 'bg-[#EFE9FB] text-[#5B3FA8]' };
    if (ext === 'zip') return { label: 'Archive', icon: 'file-archive', tone: 'bg-cream text-ink' };
    if (ext === 'pdf') return { label: 'PDF', icon: 'file-text', tone: 'bg-[#FDECEC] text-[#B42318]' };
    return { label: 'Document', icon: 'file-text', tone: 'bg-pill-navyBg text-brand-navy' };
  };

  // ---------- session overrides (status changes and new messages) ----------
  const KEY = 'banda.tasks.v1';
  function readStore() { try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch { return {}; } }
  function writeStore(s) { try { sessionStorage.setItem(KEY, JSON.stringify(s)); } catch { /* storage blocked: changes last until reload */ } }
  let store = readStore();

  function withOverrides(t) {
    const o = store[t.id] || {};
    return Object.assign({}, t, { status: o.status || t.status, messages: t.messages.concat(o.messages || []), files: t.files.concat(o.files || []) });
  }
  const all = () => TASKS.map(withOverrides);
  const find = id => { const t = TASKS.find(x => x.id === id); return t ? withOverrides(t) : null; };
  function update(id, change) {
    const o = store[id] || {};
    if (change.status) o.status = change.status;
    if (change.message) o.messages = (o.messages || []).concat([change.message]);
    if (change.files) o.files = (o.files || []).concat(change.files);
    store[id] = o; writeStore(store);
  }

  const deliverables = () => DELIVERABLES.map(d => Object.assign({}, d, { status: (store['d:' + d.id] || {}).status || d.status }));
  const deliverablesFor = taskId => deliverables().filter(d => d.task === taskId);
  function setDeliverable(id, status) { store['d:' + id] = { status }; writeStore(store); }

  // ---------- plan, usage and billing (sample account on the Growth plan) ----------
  const PLANS = {
    starter: { name: 'Starter', price: 65000, tasks: 7, legal: 1, legalPer: 'quarter' },
    growth: { name: 'Growth', price: 97500, tasks: 20, legal: 1, legalPer: 'month' },
    dedicated: { name: 'Dedicated', price: 260000, tasks: 30, legal: 4, legalPer: 'month' },
  };
  // Current billing cycle (it starts on the activation date, Terms 3.1). Legal & IP work,
  // including company and trademark filings, uses the legal review allowance (Terms 3.3).
  // Specialized Add-Ons are quoted separately and don't use the plan.
  const ACCOUNT = {
    plan: 'growth', cycleStart: '2026-10-28', renews: '2026-11-28',
    tasksUsed: 14, legalUsed: 1,
    byCat: { business: 6, ops: 3, creator: 3, tax: 2 },
    // tasks used so far, day by day from the start of the cycle
    daily: [0, 1, 1, 2, 3, 3, 4, 5, 6, 6, 7, 8, 9, 10, 11, 12, 14],
    previous: { label: '28 Sep – 27 Oct', used: 17, legalUsed: 1, daily: [0, 1, 2, 2, 3, 4, 4, 5, 6, 7, 7, 8, 8, 9, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15, 16, 16, 16, 17, 17] },
    card: { brand: 'Visa', last4: '4081', expires: '09/2028' },
  };
  // Each invoice states whether it includes VAT (Terms 5.2). Sample values.
  const INVOICES = [
    { id: 'INV-2026-0052', vat: 'VAT exclusive', date: '2026-11-06', due: '2026-11-20', desc: 'Social media management (add-on quote)', task: 'REQ-2026-0047', amount: 120000, status: 'due' },
    { id: 'INV-2026-0051', vat: 'VAT exclusive', date: '2026-10-28', due: '2026-10-28', desc: 'Growth plan, 28 Oct – 27 Nov', amount: 97500, status: 'paid', paidOn: '2026-10-28' },
    { id: 'INV-2026-0049', vat: 'VAT exclusive', date: '2026-10-28', due: '2026-11-04', desc: 'Bookkeeping support (add-on)', task: 'REQ-2026-0045', amount: 45000, status: 'paid', paidOn: '2026-10-29' },
    { id: 'INV-2026-0046', vat: 'VAT exclusive', date: '2026-09-28', due: '2026-09-28', desc: 'Growth plan, 28 Sep – 27 Oct', amount: 97500, status: 'paid', paidOn: '2026-09-28' },
    { id: 'INV-2026-0041', vat: 'VAT exclusive', date: '2026-08-28', due: '2026-08-28', desc: 'Growth plan, 28 Aug – 27 Sep', amount: 97500, status: 'paid', paidOn: '2026-08-28' },
    { id: 'INV-2026-0036', vat: 'VAT exclusive', date: '2026-07-28', due: '2026-07-28', desc: 'Starter plan, 28 Jul – 27 Aug', amount: 65000, status: 'paid', paidOn: '2026-07-28' },
  ];
  const ISTATUS = {
    due: { label: 'Due', chip: 'bg-pill-orangeBg text-brand-orangeDark' },
    overdue: { label: 'Overdue', chip: 'bg-[#FDECEC] text-[#B42318]' },
    paid: { label: 'Paid', chip: 'bg-pill-greenBg text-pill-greenTx' },
  };
  function invoices() {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    return INVOICES.map(i => {
      const o = store['i:' + i.id] || {};
      const v = Object.assign({}, i, o);
      if (v.status === 'due' && new Date(v.due + 'T23:59') < today) v.status = 'overdue';
      return v;
    });
  }
  function payInvoice(id) { store['i:' + id] = { status: 'paid', paidOn: nowLocal().slice(0, 10) }; writeStore(store); }
  const invoiceForTask = taskId => invoices().find(i => i.task === taskId);
  const getPref = (k, fallback) => (k in (store.prefs || {}) ? store.prefs[k] : fallback);
  function setPref(k, v) { store.prefs = Object.assign({}, store.prefs, { [k]: v }); writeStore(store); }

  // ---------- support ----------
  // Fill these in to show them across Help and Contact. Empty values are hidden.
  // From the Terms of Service (3.6, 6.5, 10.5) and the Privacy Policy.
  const SUPPORT = { email: 'support@bandaessentials.com', privacy: 'dpo@bandaessentials.com', phone: '', hours: 'Mon–Fri, 9am–5pm WAT', replyWithin: '24 hours' };
  const TOPICS = {
    billing: { label: 'Billing & invoices', icon: 'receipt-text' },
    tasks: { label: 'Tasks & requests', icon: 'square-check' },
    files: { label: 'Files & deliverables', icon: 'folder' },
    plan: { label: 'Plan & usage', icon: 'gauge' },
    account: { label: 'Your account & sign-in', icon: 'user' },
    privacy: { label: 'Your data & privacy', icon: 'shield' },
    technical: { label: 'Something isn’t working', icon: 'wrench' },
    other: { label: 'Something else', icon: 'circle-help' },
  };
  const TSTATUS = {
    open: { label: 'Open', chip: 'bg-pill-navyBg text-brand-navy' },
    progress: { label: 'In progress', chip: 'bg-[#EFE9FB] text-[#5B3FA8]' },
    waiting: { label: 'Waiting on you', chip: 'bg-pill-orangeBg text-brand-orangeDark' },
    resolved: { label: 'Resolved', chip: 'bg-pill-greenBg text-pill-greenTx' },
  };
  const TICKETS = [
    { id: 'SUP-2026-0048', topic: 'billing', subject: 'Bank transfer not showing on invoice', status: 'progress', created: '2026-11-11T10:24', updated: '2026-11-12T14:15',
      body: 'I paid INV-2026-0049 by bank transfer but it still showed as due for a day.', reply: 'Thanks, we’ve found the transfer and are matching it to the invoice. We’ll confirm today.' },
    { id: 'SUP-2026-0047', topic: 'account', subject: 'Sign-in code not arriving on WhatsApp', status: 'waiting', created: '2026-11-11T15:42', updated: '2026-11-12T09:10',
      body: 'My one-time sign-in code isn’t coming through on WhatsApp.', reply: 'We’ve checked and the codes are being sent. Could you confirm the WhatsApp number on your account ends in 4567? You can also choose to get the code by SMS or email.' },
    { id: 'SUP-2026-0046', topic: 'files', subject: 'Can’t open the October summary', status: 'resolved', created: '2026-11-05T11:12', updated: '2026-11-05T16:33',
      body: 'The October summary PDF won’t open on my phone.', reply: 'We re-exported the file. It should open everywhere now.' },
    { id: 'SUP-2026-0045', topic: 'plan', subject: 'More tasks this month?', status: 'resolved', created: '2026-10-25T09:18', updated: '2026-10-25T12:40',
      body: 'Can we get a few more tasks this month without changing plan?', reply: 'Extra tasks wait for your renewal, or you can move up a plan any time from Plan & Usage.' },
  ];
  const listOr = (key, fallback) => (Array.isArray(store[key]) ? store[key] : fallback.map(x => Object.assign({}, x)));
  const getList = key => listOr(key, { tickets: TICKETS }[key] || []);
  function setList(key, arr) { store[key] = arr; writeStore(store); }

  // ---------- formatting ----------
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const toDate = s => new Date(s.length === 10 ? s + 'T12:00' : s);
  const fmtDate = s => toDate(s).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  const fmtDateTime = s => toDate(s).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
  const fmtSize = b => b < 1048576 ? Math.max(1, Math.round(b / 1024)) + ' KB' : (b / 1048576).toFixed(1) + ' MB';
  const naira = n => '₦' + n.toLocaleString('en-NG');
  const pad = n => String(n).padStart(2, '0');
  const nowLocal = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  // A small announcement at the bottom of the screen (needs <div id="toast" role="status"> on the page).
  let toastTimer;
  function toast(text) {
    const el = document.getElementById('toast'); if (!el) return;
    el.textContent = text; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
  }
  const chip = (cls, text) => `<span class="chip-status ${cls}">${esc(text)}</span>`;

  window.BANDA = { CATS, STATUS, CANCELLABLE, PRIORITY, all, find, update, esc, toDate, fmtDate, fmtDateTime, fmtSize, naira, chip,
    DSTATUS, DOWNLOADABLE, REVISION_NOTE, deliverables, deliverablesFor, setDeliverable, fileType, nowLocal, toast,
    PLANS, ACCOUNT, ISTATUS, invoices, payInvoice, invoiceForTask, getPref, setPref,
    SUPPORT, TOPICS, TSTATUS, getList, setList };
})();
