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
      deliverables: [['October_Books.xlsx', 154000], ['October_Summary.pdf', 210000]],
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
      files: [['Inventory_2026.xlsx', 230000]], deliverables: [['Inventory_Tracker.xlsx', 260000]], messages: [] },
    { id: 'REQ-2026-0041', title: 'NDA review', cat: 'legal', service: 'NDA', status: 'delivered', priority: 'Medium',
      submitted: '2026-10-22T09:45', due: '2026-10-30',
      details: 'Review the NDA from a distributor before we share our recipes.',
      files: [['Distributor_NDA.pdf', 320000]], deliverables: [['NDA_Review_Notes.pdf', 180000]], messages: [] },
    { id: 'REQ-2026-0040', title: 'Q3 income summary', cat: 'tax', service: 'Income summary', status: 'checking', priority: 'Medium',
      submitted: '2026-11-01T12:20', due: '2026-11-14',
      details: 'Compile July to September income from our invoices for our accountant.',
      files: [['Q3_Invoices.zip', 5600000]], messages: [] },
    { id: 'REQ-2026-0039', title: 'Abuja trip: flights and hotel', cat: 'ops', service: 'Flight and hotel booking', status: 'delivered', priority: 'High',
      submitted: '2026-10-20T08:30', due: '2026-10-28',
      details: 'Return flights Lagos–Abuja for 2 people, 27–29 Oct, and a hotel near the Wuse 2 office.',
      files: [], deliverables: [['Abuja_Itinerary.pdf', 140000]], messages: [] },
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

  // ---------- session overrides (status changes and new messages) ----------
  const KEY = 'banda.tasks.v1';
  function readStore() { try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch { return {}; } }
  function writeStore(s) { try { sessionStorage.setItem(KEY, JSON.stringify(s)); } catch { /* storage blocked: changes last until reload */ } }
  let store = readStore();

  function withOverrides(t) {
    const o = store[t.id] || {};
    return Object.assign({}, t, { status: o.status || t.status, messages: t.messages.concat(o.messages || []) });
  }
  const all = () => TASKS.map(withOverrides);
  const find = id => { const t = TASKS.find(x => x.id === id); return t ? withOverrides(t) : null; };
  function update(id, change) {
    const o = store[id] || {};
    if (change.status) o.status = change.status;
    if (change.message) o.messages = (o.messages || []).concat([change.message]);
    store[id] = o; writeStore(store);
  }

  // ---------- formatting ----------
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const toDate = s => new Date(s.length === 10 ? s + 'T12:00' : s);
  const fmtDate = s => toDate(s).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  const fmtDateTime = s => toDate(s).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
  const fmtSize = b => b < 1048576 ? Math.max(1, Math.round(b / 1024)) + ' KB' : (b / 1048576).toFixed(1) + ' MB';
  const naira = n => '₦' + n.toLocaleString('en-NG');
  const chip = (cls, text) => `<span class="chip-status ${cls}">${esc(text)}</span>`;

  window.BANDA = { CATS, STATUS, CANCELLABLE, PRIORITY, all, find, update, esc, toDate, fmtDate, fmtDateTime, fmtSize, naira, chip };
})();
