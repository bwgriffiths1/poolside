You are grading a summary produced by an automated pipeline for an energy
market analyst whose portfolio spans thermal generation, demand response, and
retail load across ISO-NE, NYISO, and PJM. The intended reader is
energy-literate but does NOT follow this particular committee or docket.

[SOURCE MATERIAL — the text the summary was produced from]
{source}

---

[SUMMARY UNDER EVALUATION]
{summary}

---

Score the summary on five dimensions, each an integer 1–5 (5 = excellent):

- faithfulness: every number, date, attribution, and claim is supported by
  the source; nothing is invented or distorted. Any hallucinated or wrong
  fact caps this at 2.
- completeness: the decision-relevant content is covered — proposals, votes,
  positions, deadlines, price/settlement effects. Judge coverage of what
  MATTERS, not raw detail volume.
- context_sufficiency: a reader who does not follow this committee/docket
  can land — acronyms expanded on first use, initiatives given one
  orientation clause, the "why now" is clear.
- actionability: the summary surfaces what a market participant should DO or
  WATCH — deadlines, votes, filings, exposure changes — rather than only
  describing documents.
- format: TLDR discipline (single crisp opening line when present), clean
  structure, proportionate length, no meta-commentary or filler.

Also list up to 3 specific defects (empty list if none), each one sentence
naming the exact claim or omission.

Return ONLY a JSON object, no markdown fences, exactly this shape:
{{"faithfulness": n, "completeness": n, "context_sufficiency": n,
"actionability": n, "format": n, "overall": n, "defects": ["…"],
"rationale": "one or two sentences"}}
