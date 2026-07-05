// =====================================================================
// REVIEW ARTIFACT — NOT APP CODE.
// Fake data for the ENG-004 UI prototype gate (SES-004, 2026-07-05).
// Everything here is invented for review purposes only.
// =====================================================================

const MENTOR = { name: "Frank Delgado", initials: "FD", email: "frank.delgado@cbmentors.org" };

// --- Engagements (REQ-072 columns; REQ-075 status vocabulary) -----------
// Triage order the fake data leans into: pending acceptances first, then
// imminent sessions, then open action items (REQ-072 notes).
const ENGAGEMENTS = [
  {
    id: "ENG-1041", name: "Riverbend Bakery", status: "Pending Acceptance",
    contact: "Maria Santos", email: "maria@riverbendbakery.com",
    lastSession: null, nextSession: null, totalSessions: 0,
    clientId: "CL-01", companyId: "CO-01", contactIds: ["CT-01", "CT-02"],
    summary: "New assignment. Family bakery in Rocky River looking to add wholesale accounts; needs pricing and staffing guidance.",
  },
  {
    id: "ENG-1042", name: "Lakeshore Metal Works", status: "Pending Acceptance",
    contact: "Ed Kowalski", email: "ed@lakeshoremetal.com",
    lastSession: null, nextSession: null, totalSessions: 0,
    clientId: "CL-02", companyId: "CO-02", contactIds: ["CT-03"],
    summary: "New assignment. Job shop in Euclid; owner wants succession planning help before retiring in 3 years.",
  },
  {
    id: "ENG-1027", name: "Summit Auto Detail", status: "Active",
    contact: "Jerome Willis", email: "jerome@summitautodetail.com",
    lastSession: "2026-06-24", nextSession: "2026-07-06 10:00", totalSessions: 5,
    clientId: "CL-03", companyId: "CO-03", contactIds: ["CT-04", "CT-05"],
    summary: "Mobile detailing business scaling from 2 to 5 crews. Working through hiring, scheduling software, and cash-flow discipline.",
  },
  {
    id: "ENG-1030", name: "Glenview Landscaping", status: "Active",
    contact: "Tom Herrera", email: "tom@glenviewlandscape.com",
    lastSession: "2026-06-30", nextSession: "2026-07-08 14:00", totalSessions: 3,
    clientId: "CL-04", companyId: "CO-04", contactIds: ["CT-06"],
    summary: "Seasonal landscaping crew moving into year-round contracts (snow removal). Bid pricing and equipment financing on the table.",
  },
  {
    id: "ENG-1018", name: "Cedar Point Consulting", status: "Active",
    contact: "Angela Wu", email: "angela@cedarpointconsult.com",
    lastSession: "2026-06-18", nextSession: null, totalSessions: 7,
    clientId: "CL-05", companyId: "CO-05", contactIds: ["CT-07"],
    summary: "Solo HR consultant productizing her services. Three open action items from the last call; next session not yet scheduled.",
  },
  {
    id: "ENG-1033", name: "Northcoast Brewing Supply", status: "Active",
    contact: "Dan Fitzgerald", email: "dan@northcoastbrew.com",
    lastSession: "2026-07-01", nextSession: "2026-07-15 11:00", totalSessions: 4,
    clientId: "CL-06", companyId: "CO-06", contactIds: ["CT-08", "CT-09"],
    summary: "Homebrew retail + wholesale hybrid. Inventory system selection settled; now working the e-commerce relaunch.",
  },
  {
    id: "ENG-1011", name: "Euclid Ave Tailors", status: "Active",
    contact: "Rosa Marchetti", email: "rosa@euclidavetailors.com",
    lastSession: "2026-06-10", nextSession: "2026-07-20 09:00", totalSessions: 9,
    clientId: "CL-07", companyId: "CO-07", contactIds: ["CT-10"],
    summary: "Alterations shop adding made-to-measure line. Longest-running engagement; steady monthly cadence.",
  },
  {
    id: "ENG-1044", name: "Ohio City Bike Repair", status: "Assigned",
    contact: "Kevin O'Brien", email: "kevin@ohiocitybikes.com",
    lastSession: null, nextSession: null, totalSessions: 0,
    clientId: "CL-08", companyId: "CO-08", contactIds: ["CT-11"],
    summary: "Accepted this week. Intro email not yet sent; first session not yet scheduled.",
  },
  {
    id: "ENG-1036", name: "Tremont Coffee Roasters", status: "Active",
    contact: "Sarah Kim", email: "sarah@tremontcoffee.com",
    lastSession: "2026-06-27", nextSession: "2026-07-22 15:30", totalSessions: 2,
    clientId: "CL-09", companyId: "CO-09", contactIds: ["CT-12", "CT-13"],
    summary: "Wholesale roaster opening first retail cafe. Lease review done; buildout budget in progress.",
  },
  {
    id: "ENG-1022", name: "Fairview Home Health", status: "Active",
    contact: "Gloria Adams", email: "gloria@fairviewhomehealth.com",
    lastSession: "2026-06-20", nextSession: null, totalSessions: 6,
    clientId: "CL-10", companyId: "CO-10", contactIds: ["CT-14"],
    summary: "Home-care agency at 12 caregivers; billing backlog cleared, now tackling caregiver retention.",
  },
  {
    id: "ENG-1008", name: "Maple Heights Daycare", status: "On Hold",
    contact: "Denise Carter", email: "denise@mapleheightsdaycare.com",
    lastSession: "2026-05-14", nextSession: null, totalSessions: 8,
    clientId: "CL-11", companyId: "CO-11", contactIds: ["CT-15"],
    summary: "Client requested a pause through summer enrollment season. Resume check-in scheduled for August.",
  },
  {
    id: "ENG-1014", name: "Birchwood HVAC", status: "On Hold",
    contact: "Mike Tanner", email: "mike@birchwoodhvac.com",
    lastSession: "2026-05-28", nextSession: null, totalSessions: 4,
    clientId: "CL-12", companyId: "CO-12", contactIds: ["CT-16"],
    summary: "Owner in peak season; asked to pause until fall. Financing homework outstanding.",
  },
  {
    id: "ENG-1003", name: "Westside Print Shop", status: "Dormant",
    contact: "Bill Novak", email: "bill@westsideprint.com",
    lastSession: "2026-03-19", nextSession: null, totalSessions: 5,
    clientId: "CL-13", companyId: "CO-13", contactIds: ["CT-17"],
    summary: "No response to three outreach attempts since March. Dormancy follow-up email drafted.",
  },
  {
    id: "ENG-1006", name: "Shaker Square Books", status: "Dormant",
    contact: "Paul Green", email: "paul@shakersquarebooks.com",
    lastSession: "2026-04-02", nextSession: null, totalSessions: 3,
    clientId: "CL-14", companyId: "CO-14", contactIds: ["CT-18"],
    summary: "Stopped responding after the April session. Last note flagged possible store sale.",
  },
];

// --- Related records for click-through pop-ups (REQ-073) ----------------
const COMPANIES = {
  "CO-01": { name: "Riverbend Bakery LLC", type: "Client", industry: "Food & Beverage — Retail Bakery", address: "19402 Detroit Rd, Rocky River, OH 44116", phone: "(440) 555-0161", website: "riverbendbakery.com", employees: 8, founded: 2014 },
  "CO-02": { name: "Lakeshore Metal Works Inc", type: "Client", industry: "Manufacturing — Precision Job Shop", address: "1200 E 260th St, Euclid, OH 44132", phone: "(216) 555-0134", website: "lakeshoremetal.com", employees: 22, founded: 1988 },
  "CO-03": { name: "Summit Auto Detail LLC", type: "Client", industry: "Automotive Services", address: "3348 Broadview Rd, Cleveland, OH 44109", phone: "(216) 555-0177", website: "summitautodetail.com", employees: 11, founded: 2019 },
  "CO-04": { name: "Glenview Landscaping Co", type: "Client", industry: "Landscaping & Snow Services", address: "7710 Granger Rd, Valley View, OH 44125", phone: "(216) 555-0148", website: "glenviewlandscape.com", employees: 15, founded: 2011 },
  "CO-05": { name: "Cedar Point Consulting", type: "Client", industry: "Professional Services — HR", address: "815 Superior Ave, Suite 1200, Cleveland, OH 44114", phone: "(216) 555-0122", website: "cedarpointconsult.com", employees: 1, founded: 2021 },
  "CO-06": { name: "Northcoast Brewing Supply", type: "Client", industry: "Specialty Retail & Wholesale", address: "5401 Detroit Ave, Cleveland, OH 44102", phone: "(216) 555-0193", website: "northcoastbrew.com", employees: 6, founded: 2016 },
  "CO-07": { name: "Euclid Ave Tailors", type: "Client", industry: "Apparel Services", address: "12210 Euclid Ave, Cleveland, OH 44106", phone: "(216) 555-0115", website: "euclidavetailors.com", employees: 4, founded: 2002 },
  "CO-08": { name: "Ohio City Bike Repair", type: "Client", industry: "Retail — Bicycle Sales & Service", address: "2839 Lorain Ave, Cleveland, OH 44113", phone: "(216) 555-0139", website: "ohiocitybikes.com", employees: 5, founded: 2018 },
  "CO-09": { name: "Tremont Coffee Roasters", type: "Client", industry: "Food & Beverage — Coffee", address: "2406 Professor Ave, Cleveland, OH 44113", phone: "(216) 555-0158", website: "tremontcoffee.com", employees: 9, founded: 2020 },
  "CO-10": { name: "Fairview Home Health LLC", type: "Client", industry: "Healthcare — Home Care", address: "21851 Center Ridge Rd, Rocky River, OH 44116", phone: "(440) 555-0171", website: "fairviewhomehealth.com", employees: 14, founded: 2015 },
  "CO-11": { name: "Maple Heights Daycare Center", type: "Client", industry: "Childcare & Education", address: "5390 Northfield Rd, Maple Heights, OH 44137", phone: "(216) 555-0126", website: "mapleheightsdaycare.com", employees: 12, founded: 2009 },
  "CO-12": { name: "Birchwood HVAC Services", type: "Client", industry: "Construction Trades — HVAC", address: "9001 Brookpark Rd, Cleveland, OH 44129", phone: "(216) 555-0184", website: "birchwoodhvac.com", employees: 7, founded: 2013 },
  "CO-13": { name: "Westside Print Shop", type: "Client", industry: "Commercial Printing", address: "11623 Lorain Ave, Cleveland, OH 44111", phone: "(216) 555-0142", website: "westsideprint.com", employees: 3, founded: 1996 },
  "CO-14": { name: "Shaker Square Books", type: "Client", industry: "Retail — Bookstore", address: "13217 Shaker Square, Cleveland, OH 44120", phone: "(216) 555-0119", website: "shakersquarebooks.com", employees: 4, founded: 2005 },
};

const CONTACTS = {
  "CT-01": { name: "Maria Santos", role: "Owner", companyId: "CO-01", email: "maria@riverbendbakery.com", phone: "(440) 555-0161", notes: "Primary contact. Prefers early-morning calls (bakery hours)." },
  "CT-02": { name: "Luis Santos", role: "Co-owner / Operations", companyId: "CO-01", email: "luis@riverbendbakery.com", phone: "(440) 555-0162", notes: "Handles wholesale logistics questions." },
  "CT-03": { name: "Ed Kowalski", role: "Owner / President", companyId: "CO-02", email: "ed@lakeshoremetal.com", phone: "(216) 555-0134", notes: "Retiring in ~3 years; succession is the engagement driver." },
  "CT-04": { name: "Jerome Willis", role: "Owner", companyId: "CO-03", email: "jerome@summitautodetail.com", phone: "(216) 555-0177", notes: "Primary contact. Very responsive by text." },
  "CT-05": { name: "Tasha Willis", role: "Office Manager", companyId: "CO-03", email: "tasha@summitautodetail.com", phone: "(216) 555-0178", notes: "Owns scheduling and books; joins finance-topic sessions." },
  "CT-06": { name: "Tom Herrera", role: "Owner", companyId: "CO-04", email: "tom@glenviewlandscape.com", phone: "(216) 555-0148", notes: "In the field most days; afternoon sessions only." },
  "CT-07": { name: "Angela Wu", role: "Principal", companyId: "CO-05", email: "angela@cedarpointconsult.com", phone: "(216) 555-0122", notes: "Solo practice. Fast mover — sends homework back early." },
  "CT-08": { name: "Dan Fitzgerald", role: "Owner", companyId: "CO-06", email: "dan@northcoastbrew.com", phone: "(216) 555-0193", notes: "Primary contact." },
  "CT-09": { name: "Amy Chen", role: "E-commerce Lead", companyId: "CO-06", email: "amy@northcoastbrew.com", phone: "(216) 555-0194", notes: "Driving the online relaunch; joined last two sessions." },
  "CT-10": { name: "Rosa Marchetti", role: "Owner", companyId: "CO-07", email: "rosa@euclidavetailors.com", phone: "(216) 555-0115", notes: "Second-generation owner." },
  "CT-11": { name: "Kevin O'Brien", role: "Owner", companyId: "CO-08", email: "kevin@ohiocitybikes.com", phone: "(216) 555-0139", notes: "New assignment; intro call pending." },
  "CT-12": { name: "Sarah Kim", role: "Owner / Head Roaster", companyId: "CO-09", email: "sarah@tremontcoffee.com", phone: "(216) 555-0158", notes: "Primary contact." },
  "CT-13": { name: "James Park", role: "Business Partner", companyId: "CO-09", email: "james@tremontcoffee.com", phone: "(216) 555-0159", notes: "Handles the retail buildout." },
  "CT-14": { name: "Gloria Adams", role: "Administrator / Owner", companyId: "CO-10", email: "gloria@fairviewhomehealth.com", phone: "(440) 555-0171", notes: "RN background; strong on care, building business ops." },
  "CT-15": { name: "Denise Carter", role: "Director / Owner", companyId: "CO-11", email: "denise@mapleheightsdaycare.com", phone: "(216) 555-0126", notes: "Paused for summer enrollment; resume in August." },
  "CT-16": { name: "Mike Tanner", role: "Owner", companyId: "CO-12", email: "mike@birchwoodhvac.com", phone: "(216) 555-0184", notes: "Peak season pause; financing homework outstanding." },
  "CT-17": { name: "Bill Novak", role: "Owner", companyId: "CO-13", email: "bill@westsideprint.com", phone: "(216) 555-0142", notes: "Unresponsive since March." },
  "CT-18": { name: "Paul Green", role: "Owner", companyId: "CO-14", email: "paul@shakersquarebooks.com", phone: "(216) 555-0119", notes: "Possible store sale mentioned in April." },
};

const CLIENTS = {
  "CL-01": { companyId: "CO-01", since: "2026-06", program: "Core Mentoring", referral: "SCORE workshop", stage: "Startup growth" },
  "CL-02": { companyId: "CO-02", since: "2026-06", program: "Core Mentoring", referral: "GCP referral", stage: "Succession" },
  "CL-03": { companyId: "CO-03", since: "2026-02", program: "Core Mentoring", referral: "Client referral (Glenview)", stage: "Scaling" },
  "CL-04": { companyId: "CO-04", since: "2026-03", program: "Core Mentoring", referral: "Website inquiry", stage: "Diversification" },
  "CL-05": { companyId: "CO-05", since: "2025-11", program: "Core Mentoring", referral: "Chamber of Commerce", stage: "Productization" },
  "CL-06": { companyId: "CO-06", since: "2026-01", program: "Core Mentoring", referral: "Bank partner", stage: "Channel expansion" },
  "CL-07": { companyId: "CO-07", since: "2025-08", program: "Core Mentoring", referral: "Returning client", stage: "New product line" },
  "CL-08": { companyId: "CO-08", since: "2026-07", program: "Core Mentoring", referral: "SBA workshop", stage: "Intake" },
  "CL-09": { companyId: "CO-09", since: "2026-05", program: "Core Mentoring", referral: "Client referral (Northcoast)", stage: "Retail launch" },
  "CL-10": { companyId: "CO-10", since: "2025-12", program: "Core Mentoring", referral: "Healthcare network", stage: "Retention & ops" },
  "CL-11": { companyId: "CO-11", since: "2025-09", program: "Core Mentoring", referral: "Community center", stage: "On hold" },
  "CL-12": { companyId: "CO-12", since: "2026-02", program: "Core Mentoring", referral: "Trade association", stage: "On hold" },
  "CL-13": { companyId: "CO-13", since: "2025-10", program: "Core Mentoring", referral: "Walk-in event", stage: "Dormant" },
  "CL-14": { companyId: "CO-14", since: "2025-11", program: "Core Mentoring", referral: "Chamber of Commerce", stage: "Dormant" },
};

// --- Sessions with notes + action items (REQ-074, REQ-081, REQ-082) -----
// Notes are per-session; engagements aggregate them (REQ-074).
const SESSIONS = {
  "ENG-1027": [
    {
      id: "S-2701", date: "2026-07-06 10:00", status: "Scheduled", conferenceLink: "https://zoom.us/j/98123456701",
      notes: null, actionItems: null,
    },
    {
      id: "S-2605", date: "2026-06-24 10:00", status: "Held", conferenceLink: "https://zoom.us/j/98123456700",
      notes: "Reviewed June cash position — first month with all three new crews billed out. Jerome is quoting fleet contracts (two car dealerships, one funeral home). Walked through quote structure: labor hours per vehicle class, travel time, chemicals. Tasha joined for the second half; QuickBooks class tracking now separates crews. Discussed whether the 5th crew hire waits until the dealership contract signs — agreed it does.",
      actionItems: "<ul><li>Jerome: send both dealership quotes by 6/27 using the per-vehicle-class template</li><li>Tasha: run June crew-level P&L from class tracking, bring to next session</li><li>Frank: intro Jerome to the Glenview owner re: shared winter storage bay</li></ul>",
    },
    {
      id: "S-2510", date: "2026-06-10 10:00", status: "Held", conferenceLink: "https://zoom.us/j/98123456699",
      notes: "Hiring: crew lead #4 accepted, starts 6/16. Went through the onboarding checklist we built in May — Jerome added a ride-along day, good instinct. Scheduling software decision closed: Jobber, annual plan. Cash-flow: reviewed the 13-week forecast; the truck loan payment moves to the 15th to smooth the trough. Discussed pricing the interior-only package.",
      actionItems: "<ul><li>Jerome: finish Jobber setup — import customer list before 6/20</li><li>Jerome: post the interior-only package at $129 intro price</li><li>Tasha: move truck loan autopay to the 15th (call credit union)</li></ul>",
    },
    {
      id: "S-2418", date: "2026-05-27 10:00", status: "Held", conferenceLink: "https://zoom.us/j/98123456698",
      notes: "Built the crew-lead onboarding checklist together (5 days: shadow, equipment, chemicals/safety, solo with check-ins, review). Interviewed two candidates last week — one strong. Reviewed May revenue: $41k, up 18% MoM. Biggest bottleneck is scheduling by text; committed to picking software by mid-June (Jobber vs Housecall Pro).",
      actionItems: "<ul><li>Jerome: reference-check the strong crew-lead candidate</li><li>Jerome + Tasha: demo Jobber and Housecall Pro, score against the checklist</li><li>Frank: send the hiring-offer letter template from the resource library</li></ul>",
    },
    {
      id: "S-2333", date: "2026-05-13 10:00", status: "Held", conferenceLink: "https://zoom.us/j/98123456697",
      notes: "First deep-dive on scaling plan. Current state: 3 crews, Jerome still running one personally — that's the constraint. Agreed target: Jerome off the crew by July, 5 crews by September. Mapped the hiring pipeline and pay structure (base + per-job bonus). Tasha raised workers' comp cost concern; parked for the finance session.",
      actionItems: "<ul><li>Jerome: write the crew-lead job posting, send to Frank for review</li><li>Tasha: pull workers' comp quotes at 15-employee tier</li><li>Frank: share the compensation-structure worksheet</li></ul>",
    },
    {
      id: "S-2201", date: "2026-04-29 10:00", status: "Held", conferenceLink: "https://zoom.us/j/98123456696",
      notes: "Kickoff session. Jerome's story: started detailing in his driveway 2019, now 3 mobile crews, ~$450k run rate. Wants $1M by 2028 without losing quality. Pain points: he's the scheduler, the QC, and a crew lead. Set engagement goals: (1) Jerome out of daily operations, (2) scalable hiring, (3) financial visibility per crew. Monthly cadence, second Tuesdays, 10am.",
      actionItems: "<ul><li>Jerome: send last 12 months P&L and current price sheet</li><li>Frank: draft engagement goals one-pager for sign-off</li></ul>",
    },
  ],
  "ENG-1018": [
    {
      id: "S-1807", date: "2026-06-18 13:00", status: "Held", conferenceLink: "https://meet.google.com/abc-defg-hij",
      notes: "Productization session #2. Angela narrowed to three packages: HR Compliance Audit (fixed $4,500), Handbook Rebuild ($6,000), Fractional HR retainer ($2,200/mo). Pushed her on the retainer scope boundaries — she'll write inclusion/exclusion lists. Website copy review: too consultant-speak, rewrite in client outcomes. She wants to raise the audit price after two more sold at intro rate. Next session unscheduled — she's checking her July calendar.",
      actionItems: "<ul><li>Angela: write scope boundaries for the retainer package</li><li>Angela: rewrite the three package pages in outcome language</li><li>Angela: schedule July session once calendar settles</li></ul>",
    },
    {
      id: "S-1702", date: "2026-06-04 13:00", status: "Held", conferenceLink: "https://meet.google.com/abc-defg-hii",
      notes: "Reviewed her last 10 proposals — 7 were custom-scoped, which is the margin leak. Sketched the three-package model on the call. Pricing psychology discussion: anchoring the retainer against the audit. She sold a handbook rebuild at $5,500 last week (validates the price point).",
      actionItems: "<ul><li>Angela: draft the three-package one-pager</li><li>Frank: send productized-services examples from the library</li></ul>",
    },
  ],
  "ENG-1030": [
    {
      id: "S-3003", date: "2026-06-30 14:00", status: "Held", conferenceLink: "https://zoom.us/j/97001234503",
      notes: "Snow-contract math session. Built the per-event vs seasonal pricing model for the two office parks. Tom's plow truck financing approved at 7.2%. Discussed crew retention through winter — two of his best guys leave for warm-state work every November; a winter guarantee (28 hrs/wk minimum) might hold one.",
      actionItems: "<ul><li>Tom: price both office parks seasonal with a 120% snowfall cap</li><li>Tom: talk to Marcus about the winter-hours guarantee</li><li>Frank: pull the seasonal-contract template from resources</li></ul>",
    },
    {
      id: "S-2902", date: "2026-06-09 14:00", status: "Held", conferenceLink: "https://zoom.us/j/97001234502",
      notes: "Equipment financing options review. Compared credit union loan vs dealer financing vs leasing for the plow truck. Ran the numbers: buying used with CU loan wins if the truck lasts 4+ winters. Tom will get pre-approved before the fall rush.",
      actionItems: "<ul><li>Tom: submit CU pre-approval application</li><li>Tom: get the 2019 F-350 inspected before offering</li></ul>",
    },
    {
      id: "S-2801", date: "2026-05-19 14:00", status: "Held", conferenceLink: "https://zoom.us/j/97001234501",
      notes: "Kickoff. Glenview does $600k in 8 months and nearly nothing December–March. Goal: year-round revenue via snow contracts without wrecking summer margins. Mapped current assets (3 trucks, no plows) and the local competitive picture.",
      actionItems: "<ul><li>Tom: list every commercial property within 5 miles of the yard</li><li>Frank: intro to the Birchwood owner who runs winter crews (on hold — engagement paused)</li></ul>",
    },
  ],
  "ENG-1033": [
    { id: "S-3304", date: "2026-07-15 11:00", status: "Scheduled", conferenceLink: "https://zoom.us/j/96555123404", notes: null, actionItems: null },
    {
      id: "S-3303", date: "2026-07-01 11:00", status: "Held", conferenceLink: "https://zoom.us/j/96555123403",
      notes: "E-commerce relaunch planning with Dan and Amy. Shopify migration scoped: 1,400 SKUs, wholesale portal as phase 2. Amy owns the product-data cleanup. Reviewed the abandoned-cart numbers from the old site — 71%, mostly at shipping-cost reveal. Flat-rate shipping test agreed.",
      actionItems: "<ul><li>Amy: SKU data cleanup plan by 7/8</li><li>Dan: negotiate flat-rate shipping tiers with the 3PL</li><li>Frank: share the e-commerce launch checklist</li></ul>",
    },
    {
      id: "S-3302", date: "2026-06-17 11:00", status: "Held", conferenceLink: "https://zoom.us/j/96555123402",
      notes: "Inventory system decision closed: Cin7 Core. Integration with QuickBooks confirmed. Walked through the wholesale price-list problem — three versions floating around; consolidating to one master list with tier columns.",
      actionItems: "<ul><li>Dan: sign Cin7 contract, book onboarding</li><li>Amy: consolidate the wholesale price lists into the master</li></ul>",
    },
  ],
  "ENG-1011": [
    { id: "S-1109", date: "2026-07-20 09:00", status: "Scheduled", conferenceLink: "https://zoom.us/j/95123456709", notes: null, actionItems: null },
    {
      id: "S-1108", date: "2026-06-10 09:00", status: "Held", conferenceLink: "https://zoom.us/j/95123456708",
      notes: "Made-to-measure line: first month sold 6 suits, avg $1,850. Fabric supplier terms renegotiated to net-45. Rosa's apprentice can now handle 70% of alterations, freeing Rosa for MTM fittings. Discussed appointment-only Saturdays for MTM.",
      actionItems: "<ul><li>Rosa: trial appointment-only Saturdays in July</li><li>Rosa: order the fall fabric book from the new supplier</li></ul>",
    },
  ],
  "ENG-1022": [
    {
      id: "S-2206", date: "2026-06-20 15:00", status: "Held", conferenceLink: "https://meet.google.com/xyz-uvwx-yz1",
      notes: "Retention deep-dive. Exit-interview themes from the last 4 departures: scheduling unpredictability and mileage reimbursement. Modeled a guaranteed-hours tier for the top 6 caregivers. Gloria will cost it against the turnover replacement cost (~$3,200/hire).",
      actionItems: "<ul><li>Gloria: cost the guaranteed-hours tier vs replacement costs</li><li>Gloria: raise mileage to IRS rate effective 8/1</li><li>Frank: send the caregiver-retention case study</li></ul>",
    },
  ],
  "ENG-1036": [
    { id: "S-3603", date: "2026-07-22 15:30", status: "Scheduled", conferenceLink: "https://meet.google.com/tre-mont-cf3", notes: null, actionItems: null },
    {
      id: "S-3602", date: "2026-06-27 15:30", status: "Held", conferenceLink: "https://meet.google.com/tre-mont-cf2",
      notes: "Buildout budget review with Sarah and James. Contractor bids ranged $84k–$132k for the cafe space; the middle bid includes the ventilation work the cheap one omits. Equipment list trimmed to $38k by buying the espresso machine refurbished. Total project now inside the $150k SBA loan.",
      actionItems: "<ul><li>James: reference-check the middle-bid contractor</li><li>Sarah: lock the refurb espresso machine quote (30-day hold)</li><li>Frank: review their SBA 7(a) draft package before submission</li></ul>",
    },
  ],
  "ENG-1008": [
    {
      id: "S-0808", date: "2026-05-14 12:00", status: "Held", conferenceLink: "https://zoom.us/j/94001112208",
      notes: "Pre-pause wrap-up. Summer enrollment opens next week — Denise pausing mentoring until August. State inspection passed clean. Parking-lot expansion quote in hand; revisit financing in the fall.",
      actionItems: "<ul><li>Denise: send fall re-engagement availability in early August</li><li>Frank: calendar an August resume check-in</li></ul>",
    },
  ],
  "ENG-1014": [
    {
      id: "S-1404", date: "2026-05-28 16:00", status: "Held", conferenceLink: "https://zoom.us/j/93500987604",
      notes: "Pause session — Mike heading into peak cooling season. Financing homework (equipment line of credit application) still outstanding; he'll complete it during the fall shoulder. Agreed to resume September.",
      actionItems: "<ul><li>Mike: complete the LOC application by September</li><li>Frank: check in after Labor Day</li></ul>",
    },
  ],
  "ENG-1003": [
    {
      id: "S-0305", date: "2026-03-19 10:30", status: "Held", conferenceLink: "https://zoom.us/j/92123409305",
      notes: "Reviewed Q1 numbers — flat. Bill acknowledged the succession conversation needs to happen with his brother. No response to outreach since this session.",
      actionItems: "<ul><li>Bill: schedule the succession conversation (no response since)</li></ul>",
    },
  ],
  "ENG-1006": [
    {
      id: "S-0603", date: "2026-04-02 11:00", status: "Held", conferenceLink: "https://meet.google.com/sha-kerb-ks3",
      notes: "Inventory-turn analysis. Used-book margin carrying the store; new releases underperforming. Paul mentioned — briefly, at the end — that a buyer approached him about the store. Did not want to discuss further. No response to follow-ups since.",
      actionItems: "<ul><li>Paul: send the used-vs-new sales split (never received)</li></ul>",
    },
  ],
  "ENG-1041": [], "ENG-1042": [], "ENG-1044": [],
};

// --- Home panel: admin messages (REQ-011) -------------------------------
const ADMIN_MESSAGES = [
  {
    id: "MSG-118", title: "URGENT: Mentor Summit RSVP closes Monday 7/7",
    body: "The July Mentor Summit (7/18, Corporate College East) RSVP closes Monday at noon. If you have not responded, do it now — catering counts go in Monday afternoon. Reply through the Events area or email programs@cbmentors.org.",
    postedBy: "Janet Rhodes (Program Director)", date: "2026-07-03", priority: "urgent",
    requiresAck: true, read: false, acked: false, expires: "2026-07-07",
  },
  {
    id: "MSG-117", title: "New resource: 2026 SBA loan program guide",
    body: "The resource library now carries the updated 2026 SBA 7(a) and 504 program guide, including the new express-lane thresholds. Filed under Financing. Share the client-facing summary, not the full guide.",
    postedBy: "Janet Rhodes (Program Director)", date: "2026-07-01", priority: "normal",
    requiresAck: false, read: false, acked: false, expires: "2026-08-01",
  },
  {
    id: "MSG-116", title: "Q3 engagement status review — please verify your list",
    body: "Quarterly cleanup: please verify every engagement on your list carries the right status, especially Dormant vs On Hold. Board reporting pulls from these statuses on 7/15. The distinction: On Hold = client asked to pause; Dormant = client stopped responding.",
    postedBy: "Marcus Webb (Operations)", date: "2026-06-28", priority: "normal",
    requiresAck: true, read: true, acked: false, expires: "2026-07-15",
  },
  {
    id: "MSG-115", title: "Zoom account migration complete",
    body: "All mentor Zoom meetings now run under the CBM organization account. Existing scheduled meetings were migrated automatically; your personal Zoom links no longer work for client sessions. New sessions scheduled in the app get org-hosted links automatically.",
    postedBy: "IT Support", date: "2026-06-25", priority: "normal",
    requiresAck: false, read: true, acked: false, expires: "2026-07-25",
  },
];

// --- Notification bell (REQ-014) -----------------------------------------
const NOTIFICATIONS = [
  { id: "N-91", text: "Export ready: Engagements — My Active Engagements (14 rows)", detail: "engagements-2026-07-05.xlsx", time: "Today 9:41 AM", read: false, kind: "success" },
  { id: "N-90", text: "Session invite sent to Sarah Kim (Tremont Coffee Roasters)", detail: "Jul 22, 3:30 PM — Google Meet link attached", time: "Yesterday 4:12 PM", read: false, kind: "success" },
  { id: "N-89", text: "Transcript retrieved: Northcoast Brewing Supply 7/1 session", detail: "Draft summary ready for your review", time: "Jul 1, 12:20 PM", read: true, kind: "success" },
  { id: "N-88", text: "Print job failed: Sessions list", detail: "The print service did not respond. Your list is unchanged — retry from the Sessions panel, or contact IT if it persists.", time: "Jun 30, 2:05 PM", read: true, kind: "error" },
];

// --- Dashlets on Home (REQ-011: user-chosen dashlets) --------------------
// A dashlet = any view rendered small (layout standard).
const DASHLET_PENDING = ENGAGEMENTS.filter(e => e.status === "Pending Acceptance" || e.status === "Assigned");
const DASHLET_UPCOMING = [
  { date: "Mon Jul 6, 10:00 AM", engagement: "Summit Auto Detail", contact: "Jerome Willis", link: "https://zoom.us/j/98123456701" },
  { date: "Wed Jul 8, 2:00 PM", engagement: "Glenview Landscaping", contact: "Tom Herrera", link: "https://zoom.us/j/97001234503" },
  { date: "Wed Jul 15, 11:00 AM", engagement: "Northcoast Brewing Supply", contact: "Dan Fitzgerald", link: "https://zoom.us/j/96555123404" },
  { date: "Mon Jul 20, 9:00 AM", engagement: "Euclid Ave Tailors", contact: "Rosa Marchetti", link: "https://zoom.us/j/95123456709" },
  { date: "Wed Jul 22, 3:30 PM", engagement: "Tremont Coffee Roasters", contact: "Sarah Kim", link: "https://meet.google.com/tre-mont-cf3" },
];

// Triage rank for the default sort story (REQ-072 notes: pending
// acceptances -> imminent sessions -> open action items -> the rest).
const STATUS_RANK = { "Pending Acceptance": 0, "Assigned": 1, "Active": 2, "On Hold": 3, "Dormant": 4 };
