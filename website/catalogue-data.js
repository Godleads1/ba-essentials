/* The service catalogue: categories, the services in each, and what's included.
   Shared by Submit a Task (submit-task.html) and the Service Catalogue (catalogue.html),
   so the two always list the same services. Source: the B & A Task Bank. */
(function () {
  // How each category can be requested right now.
  const AVAIL = {
    available: { label: 'Available', chip: 'bg-pill-greenBg text-pill-greenTx', dot: 'bg-pill-greenTx', about: 'Ready for new requests' },
    scoping: { label: 'Scoping first', chip: 'bg-pill-navyBg text-brand-navy', dot: 'bg-brand-navy', about: 'We review each request before work starts' },
    limited: { label: 'Limited', chip: 'bg-pill-orangeBg text-brand-orangeDark', dot: 'bg-brand-orange', about: 'Fewer slots, or some restrictions apply' },
    soon: { label: 'Coming soon', chip: 'bg-[#EFE9FB] text-[#5B3FA8]', dot: 'bg-[#7C5CC4]', about: 'Not open for requests yet' },
  };
  const CATS = {
    business: {
      name: 'Business & Admin', avail: 'available', icon: 'briefcase', tone: 'bg-pill-navyBg text-brand-navy',
      blurb: 'Documents, invoicing, scheduling, outreach and filing.',
      incl: ['Document and proposal support', 'Invoicing and payment follow-up', 'Scheduling and reminders', 'Outreach, filing and data upkeep'],
      facts: [['clock', 'Typical delivery', '2–5 business days'], ['ticket', 'From your plan', 'Uses 1 task'], ['wallet', 'Extra costs', 'None expected']],
      services: {
        'Proposals & documents': ['Proposal review and editing', 'Business plan review', 'Pitch deck content review', 'Report formatting and clean-up', 'Cover letter or executive summary', 'Presentation content editing'],
        'Scheduling & reminders': ['Book, reschedule or cancel an appointment', 'Recurring reminders (up to 25 entries)'],
        'Invoicing & payments': ['Create and send invoices', 'Payment reminder follow-ups', 'Basic reconciliation support', 'Expense report'],
        'Outreach on your behalf': ['Vendor outreach or enquiries', 'Supplier quote requests (RFQs)', 'Partnership outreach emails', 'Follow-up correspondence'],
        'Filing & data upkeep': ['Contract filing and organisation', 'Record-keeping and folder set-up', 'Cloud storage organisation', 'CRM or spreadsheet updates', 'Contact list clean-up'],
      },
    },
    legal: {
      name: 'Legal & IP Review', avail: 'scoping', icon: 'scale', tone: 'bg-pill-orangeBg text-brand-orangeDark',
      blurb: 'Contracts, CAC registration, trademarks, IP and policies.',
      incl: ['Contract review and drafting', 'CAC and business registration', 'Trademark and IP protection', 'Policies, SOPs and legal guidance'],
      facts: [['clock', 'Typical delivery', '3–7 business days'], ['ticket', 'From your plan', 'Uses 1 legal review'], ['wallet', 'Extra costs', 'Government and filing fees, if any'], ['badge-check', 'Specialist review', 'Yes. Done by a qualified legal practitioner']],
      services: {
        'Review a contract': ['Brand or sponsorship deal', 'Service agreement', 'Vendor or supplier contract', 'Freelance or consulting agreement', 'Lease agreement', 'NDA'],
        'Draft a document': ['NDA', 'Memorandum of understanding (MOU)', 'Service agreement', 'Employment contract', 'Partnership agreement', 'Shareholder agreement', 'Terms of service', 'Privacy policy', 'Independent contractor agreement'],
        'Business registration': ['CAC business name', 'CAC limited company', 'Incorporated trustees', 'Limited by guarantee', 'SCUML registration', 'Business permit application'],
        'Trademark & IP': ['Trademark search and clearance', 'Trademark application', 'Copyright registration guidance', 'Takedown notice', 'Cease-and-desist letter', 'Licensing agreement review'],
        'Policies & guidance': ['HR policy or employee handbook', 'Data protection policy (NDPA)', 'Standard operating procedures (SOPs)', 'General legal question', 'Regulatory or compliance guidance'],
        'Property documents': ['Deed of assignment', 'Deed of lease', 'Tenancy agreement', 'Agreement of sale', 'Power of attorney (land)'],
      },
    },
    ops: {
      name: 'Operations & Logistics', avail: 'available', icon: 'truck', tone: 'bg-pill-greenBg text-pill-greenTx',
      blurb: 'Vendors, travel, events, project tracking and reporting.',
      incl: ['Vendor sourcing and coordination', 'Travel and event logistics', 'Project and task tracking', 'Reports and process documents'],
      facts: [['clock', 'Typical delivery', '2–5 business days'], ['ticket', 'From your plan', 'Uses 1 task'], ['wallet', 'Extra costs', 'Bookings and vendor fees are paid by you']],
      services: {
        'Vendors & collaborators': ['Find a freelancer or vendor', 'Schedule and brief a vendor', 'Coordinate a vendor payment'],
        'Travel & events': ['Flight and hotel booking', 'Itinerary planning', 'Event venue coordination', 'Local transport', 'Visa application support'],
        'Projects & tasks': ['Project timeline', 'Task tracking and follow-up', 'Team check-in coordination', 'Deliverable tracking'],
        'Reports & processes': ['Weekly or monthly report', 'KPI tracking template', 'Process documentation', 'Workflow mapping'],
      },
    },
    creator: {
      name: 'Creator Essentials', avail: 'available', icon: 'camera', tone: 'bg-[#EFE9FB] text-[#5B3FA8]',
      blurb: 'Brand deals, sponsor invoices, collaborators and shoots.',
      incl: ['Brand deal paperwork and filing', 'Sponsor invoicing and income log', 'Collaborator outreach', 'Travel and shoot logistics'],
      facts: [['clock', 'Typical delivery', '2–5 business days'], ['ticket', 'From your plan', 'Uses 1 task (legal requests use a legal review)'], ['wallet', 'Extra costs', 'Bookings are paid by you']],
      services: {
        'Creator services': ['File brand deal contracts', 'Sponsor invoicing and income logging', 'Collaborator or vendor outreach', 'Brand deal legal review', 'Book a photographer, videographer or editor', 'Travel and shoot logistics'],
      },
    },
    tax: {
      name: 'Tax Readiness', avail: 'available', icon: 'calculator', tone: 'bg-cream text-ink',
      blurb: 'Income summaries, expense records and tax reminders.',
      incl: ['Income summary from your invoices', 'Expense categorisation', 'Quarterly tax reminders', 'An export pack for your accountant'],
      facts: [['clock', 'Typical delivery', '2–5 business days'], ['ticket', 'From your plan', 'Uses 1 task'], ['circle-alert', 'Please note', 'We prepare your records. We don’t file taxes or give tax advice']],
      services: {
        'Tax readiness': ['Income summary', 'Expense categorisation', 'Quarterly tax reminders', 'Document vault set-up', 'Export pack for your accountant'],
      },
    },
    addons: {
      name: 'Specialized Add-Ons', avail: 'soon', icon: 'sparkles', tone: 'bg-pill-navyBg text-brand-navy',
      blurb: 'Social media, bookkeeping, copywriting, CRM and translation.',
      incl: ['Social media management', 'Bookkeeping support', 'Content and copywriting', 'CRM set-up, and translation'],
      facts: [['clock', 'Typical delivery', 'Agreed with you in the quote'], ['ticket', 'From your plan', 'Priced separately. We send a quote first'], ['badge-check', 'Specialist', 'Yes. Matched to the work']],
      services: {
        'Add-on services': ['Social media management', 'Bookkeeping support', 'Content and copywriting', 'CRM set-up and management', 'Translation support'],
      },
    },
    scoping: {
      name: 'Not sure yet', avail: 'available', icon: 'circle-help', tone: 'bg-cream text-ink', hidden: true,
      blurb: '', incl: ['A coordinator reads your request', 'We suggest the right service', 'You approve before work starts'],
      facts: [['clock', 'First reply', 'Within one business day'], ['ticket', 'From your plan', 'Confirmed once scoped']],
      services: { 'Scoping': ['Help me work out what I need'] },
    },
  };
  window.CATALOGUE = { AVAIL, CATS };
})();
