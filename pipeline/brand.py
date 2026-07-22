"""
brand.py — shared editorial design tokens for the .docx exporters.

Mirrors the web reader's design system (web/src/styles/tokens.css, the
Editorial `:root` default) so the downloaded document and the on-screen
briefing read as the same product. `pipeline/briefing.py` imports these; keep
them in sync with tokens.css when the web palette moves.

Values were dialled in with the interactive tuner in
tools/briefing-design/index.html — tweak there, hit "Copy brand.py tokens",
and paste the block back over the constants below.

Page stays white (a full-page warm tint prints as a grey wash); the warmth
comes from the serif type, the terracotta accent, and the tint bands.
"""
from docx.shared import Pt, Inches, RGBColor

# ---- fonts (Office-safe families that stand in for the web's Newsreader /
#      Geist Mono; both render on any Windows/Mac Office install) ----
BODY_FONT = "Baskerville"  # serif — body + titles (finer strokes than Georgia)
LABEL_FONT = "Consolas"    # mono — eyebrows / table headers / captions / meta

# ---- colours (RGBColor for run text; bare hex strings for OXML fills/borders) ----
INK = RGBColor(0x1A, 0x18, 0x15)          # --ink        headings, emphasis
INK_SOFT = RGBColor(0x4A, 0x46, 0x40)     # --ink-soft   body prose
MUTED = RGBColor(0x8A, 0x84, 0x7A)        # --muted      labels, meta
MUTED_SOFT = RGBColor(0xB5, 0xAF, 0xA3)   # --muted-soft separators, dividers
ACCENT = RGBColor(0xC4, 0x63, 0x3A)       # --accent     terracotta
SUCCESS = RGBColor(0x4D, 0x7C, 0x4E)      # --success    positive deltas
DANGER = RGBColor(0xA4, 0x4A, 0x3C)       # --danger     negative deltas

ACCENT_HEX = "C4633A"
ACCENT_TINT = "F4E4D8"    # --accent-tint  callout / exec tint band fill
ELEV = "FBF9F4"           # --bg-elev      next-steps / soft card fill
BORDER = "E3DDD1"         # --border       hairline rules
BORDER_SOFT = "ECE6DA"    # --border-soft  faint row separators
MUTED_SOFT_HEX = "B5AFA3" # --muted-soft   grey accent bar (materials)

# ---- type scale (pt) — one ladder: 26 / 16 / 15 / 13 / 12 / 9 / 8 ----
SZ_MASTHEAD = Pt(26)      # committee title on the cover
SZ_HEADLINE = Pt(16)      # "Meeting Briefing" dek + date  (standardised w/ group)
SZ_GROUP = Pt(16)         # depth-0 agenda group header
SZ_SUBITEM = Pt(15)       # depth-1 sub-item header
SZ_H = Pt(13)             # run-in sub-heading inside a section body (= body, bold)
SZ_BODY = Pt(13)          # body prose (Baskerville reads small — 13 ≈ 11pt Georgia)
SZ_BODY_SM = Pt(12)       # table cells, callout body
SZ_EYEBROW = Pt(9)        # section eyebrow labels (KEY TAKEAWAYS …)
SZ_LABEL = Pt(9)          # micro labels (NEPOOL, MATERIALS, NEXT STEPS, table heads)
SZ_CAPTION = Pt(9)        # figure captions, materials filenames
SZ_LINK = Pt(9)           # venue / materials links
SZ_FOOTER = Pt(8)         # running footer (kept smaller than page labels)

# ---- spacing / layout ----
LINE_SPACING = 1.2        # body multiple (tighter than the 1.35 default)
SPACE_AFTER = Pt(8)       # gap after a body paragraph
MARGIN_SIDE = Inches(1.15)  # left/right — measure ~6.2", ~70 chars
MARGIN_TOPBOT = Inches(1.0)

# ---- treatments ----
EXEC_TREATMENT = "band"        # band | plain | tint
TABLE_STYLE = "borderless"     # borderless | ruled
SMALL_CAPS_GROUPS = True       # small-caps depth-0 agenda headers
