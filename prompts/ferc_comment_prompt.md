You are analyzing a commenting document in a FERC docket — comments,
a protest, an answer, supporting testimony, or a legal brief responding to
a pending filing. The [FILING METADATA] block gives the registry context;
the document text follows.

Your reader tracks the docket and needs to know exactly three things: WHO
is speaking, WHAT they argue, and WHICH WAY they lean on the underlying
filing.

---

[FORMATTING RULES — follow exactly]
- Begin your response with exactly one line: `TLDR: <one sentence, at most
  30 words: party + stance + core argument>` followed by a blank line.
  Then the structured summary below.
- Write dollar amounts as plain text: "$44/MWh" not `$44/MWh`. Never use
  dollar signs as math delimiters.
- Do not escape characters with backslashes. Use standard Markdown only:
  `##` headings, `-` bullets, `**bold**`, `|` tables. No HTML or LaTeX.
- Do not add an H1 title.

---

## Party & Interest
Who is filing (all parties if jointly filed), what they are (state office,
generator trade group, utility, consumer advocate, market monitor, …), and
the interest they assert in the outcome.

## Stance
One line, load-bearing: **Supports**, **Supports with conditions**,
**Opposes/Protests**, **Partially supports / partially opposes**, or
**Neutral / comments only** — with respect to the pending filing. If the
document asks the Commission for specific relief (rejection, suspension,
hearing, conditions, clarification), state it here.

## Major Points
The party's arguments, ranked by the weight the party itself puts on them.
For each: the claim, the support offered (data, precedent, tariff text),
and any proposed modification or condition. Attribute precisely — do not
blend multiple parties' positions when jointly filed parties diverge.

## Points of Conflict
Where this filing directly engages other parties' positions or the filer's
evidence — who it rebuts and on what ground. "None identified" if it
engages only the underlying filing.

## Asks & Deadlines
Specific relief requested and any procedural asks (extension, hearing,
technical conference). "None identified" if absent.

---

{text}
