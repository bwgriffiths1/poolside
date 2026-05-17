// Mock data modeling ISO-NE and NYISO meeting intelligence
// Shape mirrors db_new.py: meeting -> agenda_items -> documents, plus summaries

window.MOCK_DATA = (() => {
  const venues = [
    { id: 1, short_name: "ISO-NE", name: "ISO New England" },
    { id: 2, short_name: "NYISO", name: "New York ISO" },
  ];

  const types = [
    { short: "MC",   name: "Markets Committee",          venue: "ISO-NE" },
    { short: "NPC",  name: "NEPOOL Participants Committee", venue: "ISO-NE" },
    { short: "RC",   name: "Reliability Committee",      venue: "ISO-NE" },
    { short: "TC",   name: "Transmission Committee",     venue: "ISO-NE" },
    { short: "PAC",  name: "Planning Advisory Committee",venue: "ISO-NE" },
    { short: "BIC",  name: "Business Issues Committee",  venue: "NYISO"  },
    { short: "OC",   name: "Operating Committee",        venue: "NYISO"  },
    { short: "MC",   name: "Management Committee",       venue: "NYISO"  },
  ];

  // status: scheduled | materials | summarized | updated
  const meetings = [
    {
      id: 101, venue: "ISO-NE", type_short: "MC", type_name: "Markets Committee",
      title: "Markets Committee — May 2026",
      meeting_date: "2026-05-12", end_date: "2026-05-13",
      location: "Holyoke, MA · Hybrid",
      external_id: "MC-2026-05",
      status: "summarized",
      doc_count: 47, item_count: 14,
      tags: ["Capacity Accreditation", "Day-Ahead Market", "FCM"],
    },
    {
      id: 102, venue: "ISO-NE", type_short: "NPC", type_name: "NEPOOL Participants Committee",
      title: "NEPOOL Participants Committee — May 2026",
      meeting_date: "2026-05-08",
      location: "Westborough, MA",
      external_id: "NPC-2026-05",
      status: "updated",
      doc_count: 32, item_count: 11,
      tags: ["FERC Filing", "Tariff", "ESI"],
    },
    {
      id: 103, venue: "NYISO", type_short: "BIC", type_name: "Business Issues Committee",
      title: "Business Issues Committee — May 2026",
      meeting_date: "2026-05-14",
      location: "Rensselaer, NY",
      external_id: "BIC-2026-05-14",
      status: "materials",
      doc_count: 21, item_count: 9,
      tags: ["Capacity Market", "Buyer-Side Mitigation"],
    },
    {
      id: 104, venue: "ISO-NE", type_short: "RC", type_name: "Reliability Committee",
      title: "Reliability Committee — May 2026",
      meeting_date: "2026-05-20",
      location: "Holyoke, MA",
      external_id: "RC-2026-05",
      status: "materials",
      doc_count: 18, item_count: 8,
      tags: ["Winter Operations", "Resource Adequacy"],
    },
    {
      id: 105, venue: "ISO-NE", type_short: "PAC", type_name: "Planning Advisory Committee",
      title: "Planning Advisory Committee — May 2026",
      meeting_date: "2026-05-21",
      location: "Westborough, MA",
      external_id: "PAC-2026-05",
      status: "scheduled",
      doc_count: 0, item_count: 0,
      tags: [],
    },
    {
      id: 106, venue: "NYISO", type_short: "OC", type_name: "Operating Committee",
      title: "Operating Committee — May 2026",
      meeting_date: "2026-05-22",
      location: "Rensselaer, NY",
      external_id: "OC-2026-05-22",
      status: "scheduled",
      doc_count: 0, item_count: 0,
      tags: [],
    },
    {
      id: 107, venue: "ISO-NE", type_short: "TC", type_name: "Transmission Committee",
      title: "Transmission Committee — April 2026",
      meeting_date: "2026-04-29",
      location: "Holyoke, MA",
      external_id: "TC-2026-04",
      status: "summarized",
      doc_count: 24, item_count: 10,
      tags: ["Order 2023", "Interconnection Queue"],
    },
    {
      id: 108, venue: "ISO-NE", type_short: "MC", type_name: "Markets Committee",
      title: "Markets Committee — April 2026",
      meeting_date: "2026-04-08", end_date: "2026-04-09",
      location: "Holyoke, MA",
      external_id: "MC-2026-04",
      status: "summarized",
      doc_count: 51, item_count: 15,
      tags: ["Capacity Accreditation", "PRD", "ESI Phase 2"],
    },
    {
      id: 109, venue: "NYISO", type_short: "BIC", type_name: "Business Issues Committee",
      title: "Business Issues Committee — April 2026",
      meeting_date: "2026-04-16",
      location: "Rensselaer, NY",
      external_id: "BIC-2026-04-16",
      status: "summarized",
      doc_count: 19, item_count: 7,
      tags: ["DEFRs", "Tariff"],
    },
    {
      id: 110, venue: "ISO-NE", type_short: "NPC", type_name: "NEPOOL Participants Committee",
      title: "NEPOOL Participants Committee — April 2026",
      meeting_date: "2026-04-04",
      location: "Westborough, MA",
      external_id: "NPC-2026-04",
      status: "summarized",
      doc_count: 28, item_count: 12,
      tags: ["FERC Filing"],
    },
    {
      id: 111, venue: "ISO-NE", type_short: "MC", type_name: "Markets Committee",
      title: "Markets Committee — June 2026",
      meeting_date: "2026-06-10", end_date: "2026-06-11",
      location: "Holyoke, MA",
      external_id: "MC-2026-06",
      status: "scheduled",
      doc_count: 0, item_count: 0,
      tags: [],
    },
  ];

  // Detail for meeting 101 (the hero — Markets Committee May 2026)
  const meeting101 = {
    one_line: "MC advances Capacity Accreditation Phase 2 to NPC vote; ESI design framework approved; FCA 19 parameters finalized.",
    agenda: [
      {
        id: 1, item_id: "1", depth: 0, title: "Welcome and Chair Report",
        presenter: "M. Henderson", org: "ISO-NE", time_slot: "9:00 AM",
        vote_status: null, has_summary: true,
        docs: [
          { id: 1, filename: "MC_2026_05_Chair_Report.pdf", type: "pdf", assigned: true },
        ],
        one_line: "Chair previews summer reliability outlook and 2026 work plan adjustments.",
      },
      {
        id: 2, item_id: "2", depth: 0, title: "Consent Agenda",
        presenter: "J. Phelps", org: "ISO-NE", time_slot: "9:10 AM",
        vote_status: "Vote — Approved",
        has_summary: true,
        docs: [
          { id: 2, filename: "MC_April_2026_Minutes.pdf", type: "pdf", assigned: true },
          { id: 3, filename: "Consent_Agenda_Memo.pdf", type: "pdf", assigned: true },
        ],
        one_line: "April minutes and three Memorandum revisions approved without objection.",
      },
      {
        id: 3, item_id: "3", depth: 0, title: "Capacity Accreditation — Phase 2 Design",
        presenter: "C. Cardamone", org: "ISO-NE", time_slot: "9:25 AM",
        vote_status: "Vote — Recommend to NPC",
        has_summary: true, wmpp_id: "WMPP-2026-014",
        docs: [
          { id: 4, filename: "Cap_Accred_Phase2_Presentation.pptx", type: "pptx", assigned: true },
          { id: 5, filename: "Cap_Accred_Phase2_Tariff_Redline.pdf", type: "pdf", assigned: true },
          { id: 6, filename: "Cap_Accred_Phase2_Impact_Analysis.xlsx", type: "xlsx", assigned: true },
          { id: 7, filename: "NEPGA_Comments_Cap_Accred.pdf", type: "pdf", assigned: true, ceii: false },
        ],
        one_line: "Phase 2 introduces marginal ELCC for storage and hybrids; MC recommends to NPC by 78%.",
      },
      {
        id: 4, item_id: "3.1", depth: 1, title: "Marginal ELCC Methodology",
        presenter: "C. Cardamone", org: "ISO-NE", has_summary: true,
        docs: [
          { id: 8, filename: "Marginal_ELCC_Technical_Memo.pdf", type: "pdf", assigned: true },
        ],
        one_line: "Marginal vs. average ELCC trade-offs detailed; storage class accredits at 51%.",
      },
      {
        id: 5, item_id: "3.2", depth: 1, title: "Stakeholder Concerns — Imports",
        presenter: "K. Sandagger", org: "NEPGA", has_summary: true,
        docs: [],
        one_line: "NEPGA flags double-counting risk for tie-line capacity; ISO commits to clarifying language.",
      },
      {
        id: 6, item_id: "4", depth: 0, title: "Energy Storage Initiative — Phase 2",
        presenter: "A. Coppo", org: "ISO-NE", time_slot: "10:45 AM",
        vote_status: "Vote — Approved (Design)",
        has_summary: true,
        docs: [
          { id: 9, filename: "ESI_Phase2_Design_Framework.pdf", type: "pdf", assigned: true },
          { id: 10, filename: "ESI_Phase2_Examples.pdf", type: "pdf", assigned: true },
          { id: 11, filename: "ESI_FAQ_v3.pdf", type: "pdf", assigned: true },
        ],
        one_line: "Design framework approved; tariff drafting to begin June, target Q4 2026 filing.",
      },
      {
        id: 7, item_id: "5", depth: 0, title: "FCA 19 Parameters",
        presenter: "M. Knowland", org: "ISO-NE", time_slot: "1:00 PM",
        vote_status: "Vote — Approved",
        has_summary: true,
        docs: [
          { id: 12, filename: "FCA19_Parameters.pdf", type: "pdf", assigned: true },
          { id: 13, filename: "FCA19_ICR_Calculation.xlsx", type: "xlsx", assigned: true },
        ],
        one_line: "ICR set at 32,485 MW; Net CONE finalized at $11.86/kW-month.",
      },
      {
        id: 8, item_id: "6", depth: 0, title: "Day-Ahead Ancillary Services Market — Status",
        presenter: "T. Zhang", org: "ISO-NE", time_slot: "2:15 PM",
        vote_status: "Discussion",
        has_summary: true,
        docs: [
          { id: 14, filename: "DASI_Status_Report.pdf", type: "pdf", assigned: true },
          { id: 15, filename: "DASI_Performance_Q1_2026.pdf", type: "pdf", assigned: true },
        ],
        one_line: "Six months in: $43M in stakeholder savings; reserve scarcity events down 22%.",
      },
      {
        id: 9, item_id: "7", depth: 0, title: "Inverter-Based Resource Performance",
        presenter: "P. Tatro", org: "ISO-NE", time_slot: "3:00 PM",
        vote_status: "Discussion", has_summary: true,
        docs: [
          { id: 16, filename: "IBR_Performance_Update.pdf", type: "pdf", assigned: true },
        ],
        one_line: "Post-Order 901 compliance plan presented; gap analysis through 2027.",
      },
      {
        id: 10, item_id: "8", depth: 0, title: "Future Agenda Items",
        presenter: "M. Henderson", org: "ISO-NE", time_slot: "4:15 PM",
        vote_status: null, has_summary: false,
        docs: [], one_line: "",
      },
    ],
  };

  // The hero briefing — full markdown-like sections
  const briefing101 = {
    title: "Markets Committee — May 12–13, 2026",
    subtitle: "ISO New England · Holyoke, MA · Hybrid",
    headline: "Capacity Accreditation Phase 2 advances to NPC; ESI design framework approved; FCA 19 parameters finalized",
    generated_at: "2026-05-13 18:42",
    model: "claude-sonnet-4.5",
    word_count: 2840,
    reading_time: 11,
    tldr: [
      "Capacity Accreditation Phase 2 design recommended to NPC by 78% — marginal ELCC replaces average ELCC for storage and hybrid resources.",
      "Energy Storage Initiative Phase 2 design framework approved; tariff drafting begins June, Q4 2026 FERC filing targeted.",
      "FCA 19 parameters finalized — ICR set at 32,485 MW; Net CONE at $11.86/kW-month, a 4.2% increase over FCA 18.",
      "DASI six-month review shows $43M in stakeholder savings and a 22% reduction in reserve scarcity events.",
    ],
    sections: [
      {
        id: "cap-accred",
        kind: "agenda",
        item_id: "3",
        title: "Capacity Accreditation — Phase 2 Design",
        vote: "Recommend to NPC — Approved 78% / 19% / 3%",
        body: [
          { kind: "p", text: "ISO-NE staff presented the Phase 2 design for capacity accreditation, replacing the current average ELCC methodology with a marginal ELCC framework for storage and hybrid resources. The proposal is the culmination of 14 months of stakeholder engagement under WMPP-2026-014 and reflects substantive revisions following March's straw poll." },
          { kind: "h", text: "Marginal vs. average ELCC" },
          { kind: "p", text: "Under marginal ELCC, each incremental MW is accredited based on its contribution to resource adequacy at the margin rather than the system-wide average. Staff modeled the impact across resource classes: standalone 4-hour storage accredits at 51% under Phase 2 vs. 67% today; 8-hour storage rises to 79%; solar-plus-storage hybrids accredit between 58–71% depending on coupling ratio." },
          { kind: "callout", label: "Position", text: "The shift reduces accredited capacity for the existing 4-hour storage fleet by an estimated 340 MW system-wide, but creates clear price signals for longer-duration storage. Expect downward pressure on near-term FCA clearing prices for affected resources." },
          { kind: "h", text: "Stakeholder concerns" },
          { kind: "p", text: "NEPGA (Sandagger) raised concerns about double-counting risk for tie-line capacity, arguing the methodology could understate import contributions during winter scarcity. ISO committed to clarifying language in the tariff filing and a follow-up technical session in July. Three generator owners signed onto a letter requesting a one-year implementation delay; staff held firm on the November 2026 target." },
          { kind: "data", title: "Class accreditation — current vs. Phase 2", rows: [
            ["Resource class", "Current", "Phase 2", "Δ"],
            ["4-hour battery storage", "67%", "51%", "-16pp"],
            ["8-hour battery storage", "72%", "79%", "+7pp"],
            ["Solar + 4hr storage (1.0x)", "62%", "58%", "-4pp"],
            ["Solar + 4hr storage (1.5x)", "65%", "71%", "+6pp"],
            ["Onshore wind", "31%", "34%", "+3pp"],
            ["Offshore wind", "44%", "47%", "+3pp"],
          ]},
          { kind: "p", text: "The Committee voted to recommend the design to NPC with 78% support, exceeding the 60% threshold. NPC consideration is scheduled for June 5." },
        ],
        next_steps: [
          "NPC vote — June 5, 2026",
          "Tariff redline circulated week of May 26",
          "Technical session on tie-line accreditation — July 14",
        ],
      },
      {
        id: "esi-phase2",
        kind: "agenda",
        item_id: "4",
        title: "Energy Storage Initiative — Phase 2",
        vote: "Design — Approved 91% / 6% / 3%",
        body: [
          { kind: "p", text: "Adam Coppo presented the ESI Phase 2 design framework, addressing five gaps identified in the Phase 1 retrospective: (1) state-of-charge management during multi-day events, (2) co-optimization with reserves, (3) hybrid resource modeling, (4) deration during winter performance hours, and (5) settlement edge cases for fast-cycling resources." },
          { kind: "callout", label: "Position", text: "The framework is materially more favorable to storage operators than the December straw proposal — particularly on SOC management, which now allows operator-designated 'hold' hours without penalty. Expect modest uplift to storage revenues in 2027+." },
          { kind: "p", text: "The design framework was approved with 91% support. Tariff drafting begins June, with target FERC filing in Q4 2026 and effective date of June 2027. A working group will convene monthly through September to refine implementation details." },
        ],
        next_steps: [
          "Tariff working group kickoff — June 11",
          "Draft tariff circulated — September 2026",
          "Target FERC filing — Q4 2026",
        ],
      },
      {
        id: "fca19",
        kind: "agenda",
        item_id: "5",
        title: "FCA 19 Parameters",
        vote: "Approved 96% / 4% / 0%",
        body: [
          { kind: "p", text: "Matt Knowland presented final parameters for Forward Capacity Auction 19, covering capacity commitment period June 2028 – May 2029. Installed Capacity Requirement was set at 32,485 MW, a 1.8% increase over FCA 18 driven by load forecast revisions and the retirement of a 620 MW gas peaker." },
          { kind: "data", title: "FCA 19 parameters", rows: [
            ["Parameter", "FCA 18", "FCA 19", "Δ"],
            ["ICR (MW)", "31,910", "32,485", "+1.8%"],
            ["Net CONE ($/kW-mo)", "11.38", "11.86", "+4.2%"],
            ["CONE ($/kW-mo)", "18.42", "19.05", "+3.4%"],
            ["Starting price ($/kW-mo)", "22.84", "23.72", "+3.9%"],
          ]},
          { kind: "p", text: "Net CONE rose 4.2% reflecting capital cost inflation on the reference combined cycle unit; CT-based reference unit was retired from the Net CONE calculation per FERC ER25-1842. The starting price for FCA 19 will be $23.72/kW-month." },
        ],
        next_steps: [
          "Qualification window opens — July 1",
          "Show of Interest deadline — August 15",
          "FCA 19 auction — February 2027",
        ],
      },
      {
        id: "dasi",
        kind: "agenda",
        item_id: "6",
        title: "Day-Ahead Ancillary Services Market — Status",
        vote: "Discussion",
        body: [
          { kind: "p", text: "Tao Zhang presented the six-month performance review of the Day-Ahead Ancillary Services Market (DASI), which began operations November 1, 2025. Top-line: $43M in stakeholder savings and a 22% reduction in reserve scarcity events relative to the counterfactual." },
          { kind: "p", text: "Performance has been strongest during shoulder-season ramps; winter performance flagged two issues — (1) procurement curve calibration was too steep during high-confidence cold events, leading to over-procurement on Jan 18–19; (2) settlement timing created a 3-business-day lag that several participants flagged. Both will be addressed in a Q3 tariff change." },
        ],
        next_steps: [
          "Procurement curve calibration filing — August 2026",
          "Settlement timing fix — Q3 tariff change",
        ],
      },
    ],
  };

  // Recent ingest job log — for the Add Meeting screen
  const recentIngests = [
    { id: "ing-204", meeting_id: 103, status: "complete", started: "2026-05-13 09:42", finished: "2026-05-13 09:47",
      label: "NYISO BIC 2026-05-14", docs: 21, agenda_items: 9 },
    { id: "ing-203", meeting_id: 102, status: "complete", started: "2026-05-12 14:11", finished: "2026-05-12 14:18",
      label: "ISO-NE NPC 2026-05-08", docs: 32, agenda_items: 11 },
    { id: "ing-202", meeting_id: 101, status: "complete", started: "2026-05-11 08:30", finished: "2026-05-11 08:41",
      label: "ISO-NE MC 2026-05-12", docs: 47, agenda_items: 14 },
    { id: "ing-201", meeting_id: 104, status: "complete", started: "2026-05-10 17:02", finished: "2026-05-10 17:09",
      label: "ISO-NE RC 2026-05-20", docs: 18, agenda_items: 8 },
  ];

  return { venues, types, meetings, meeting101, briefing101, recentIngests };
})();
