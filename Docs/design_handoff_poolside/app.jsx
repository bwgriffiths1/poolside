// App shell — routing + tweaks panel for aesthetic/density

const DEFAULT_TWEAKS = /*EDITMODE-BEGIN*/{
  "aesthetic": "editorial",
  "density":   "comfortable",
  "accent":    "#c4633a",
  "show_dropcap": true,
  "show_kpis":  true
}/*EDITMODE-END*/;

const App = () => {
  const [route, setRoute] = React.useState(() => {
    const h = (window.location.hash || "").replace(/^#\//, "");
    if (!h) return { name: "overview" };
    const [name, id] = h.split("/");
    return { name, id: id ? Number(id) : undefined };
  });

  React.useEffect(() => {
    const onHash = () => {
      const h = (window.location.hash || "").replace(/^#\//, "");
      if (!h) return setRoute({ name: "overview" });
      const [name, id] = h.split("/");
      setRoute({ name, id: id ? Number(id) : undefined });
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const nav = (r) => {
    window.location.hash = `/${r.name}${r.id ? `/${r.id}` : ""}`;
    setRoute(r);
    const main = document.querySelector(".main");
    if (main) main.scrollTo({ top: 0 });
  };

  // ── Tweaks ──────────────────────────────────────────────────────────────
  const [tweaks, setTweak] = useTweaks(DEFAULT_TWEAKS);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-aesthetic", tweaks.aesthetic);
    document.documentElement.setAttribute("data-density", tweaks.density);
    document.documentElement.style.setProperty("--accent", tweaks.accent);
    // derive accent-tint
    const tint = mixAccentTint(tweaks.accent, tweaks.aesthetic);
    document.documentElement.style.setProperty("--accent-tint", tint);
  }, [tweaks.aesthetic, tweaks.density, tweaks.accent]);

  return (
    <div className="app">
      <Sidebar route={route} onNav={nav} />
      <main className="main">
        {route.name === "overview" && <OverviewScreen onNav={nav} />}
        {route.name === "meeting"  && <MeetingScreen  id={route.id || 101} onNav={nav} />}
        {route.name === "briefing" && <BriefingScreen id={route.id || 101} onNav={nav} />}
        {route.name === "add"      && <AddScreen      onNav={nav} />}
        {!["overview","meeting","briefing","add"].includes(route.name) && (
          <StubScreen name={route.name} onNav={nav} />
        )}
      </main>

      <TweaksPanel title="Tweaks">
        <TweakSection label="Aesthetic">
          <TweakRadio
            label="Direction"
            value={tweaks.aesthetic}
            onChange={(v) => setTweak("aesthetic", v)}
            options={[
              { value: "editorial", label: "Editorial" },
              { value: "minimal",   label: "Minimal" },
              { value: "terminal",  label: "Terminal" },
            ]}
          />
          <div className="muted text-xs" style={{ marginTop: -4, lineHeight: 1.5 }}>
            <strong>Editorial</strong> — warm cream, serif briefing.<br/>
            <strong>Minimal</strong> — neutral grays, sans throughout.<br/>
            <strong>Terminal</strong> — dark, mono-led, dense.
          </div>
        </TweakSection>

        <TweakSection label="Density">
          <TweakRadio
            label="Spacing"
            value={tweaks.density}
            onChange={(v) => setTweak("density", v)}
            options={[
              { value: "compact",     label: "Compact" },
              { value: "comfortable", label: "Default" },
              { value: "spacious",    label: "Roomy" },
            ]}
          />
        </TweakSection>

        <TweakSection label="Accent">
          <TweakColor
            label="Accent"
            value={tweaks.accent}
            onChange={(v) => setTweak("accent", v)}
            options={["#c4633a", "#2a6fdb", "#1f6f4a", "#8e4ec6", "#1a1a1a"]}
          />
        </TweakSection>

        <TweakSection label="Briefing reader">
          <TweakToggle
            label="Show drop-cap"
            value={tweaks.show_dropcap}
            onChange={(v) => setTweak("show_dropcap", v)}
          />
        </TweakSection>

        <TweakSection label="Overview">
          <TweakToggle
            label="Show KPI strip"
            value={tweaks.show_kpis}
            onChange={(v) => setTweak("show_kpis", v)}
          />
        </TweakSection>
      </TweaksPanel>
    </div>
  );
};

// Stub screens for sidebar items we didn't build
const StubScreen = ({ name, onNav }) => {
  const labels = {
    deepdive: "Deep Dive",
    bulk: "Bulk Summarize",
    prompts: "Prompt Library",
    settings: "Settings",
  };
  return (
    <>
      <Topbar crumbs={[{ label: labels[name] || name }]} actions={null} />
      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Coming soon</div>
          <h1 className="page-title">{labels[name] || name}</h1>
          <p className="page-subtitle">
            This screen wasn't part of the v1 design exploration. The four screens you can browse are:
            Overview, Meeting Detail, Briefing Reader, and Add Meeting.
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn btn-primary" onClick={() => onNav({ name: "overview" })}>← Back to Overview</button>
          <button className="btn" onClick={() => onNav({ name: "briefing", id: 101 })}>Open hero briefing</button>
        </div>
      </div>
    </>
  );
};

// Helper: derive a softer tint from accent hex for a given aesthetic
function mixAccentTint(hex, aesthetic) {
  const c = hexToRgb(hex);
  if (!c) return "#f4e4d8";
  if (aesthetic === "terminal") {
    return rgbToHex(Math.round(c.r * 0.20), Math.round(c.g * 0.18), Math.round(c.b * 0.16));
  }
  const bgR = aesthetic === "minimal" ? 252 : 246;
  const bgG = aesthetic === "minimal" ? 252 : 244;
  const bgB = aesthetic === "minimal" ? 250 : 239;
  const a = 0.20;
  return rgbToHex(
    Math.round(bgR * (1 - a) + c.r * a),
    Math.round(bgG * (1 - a) + c.g * a),
    Math.round(bgB * (1 - a) + c.b * a),
  );
}
function hexToRgb(h) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(h);
  return m ? { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) } : null;
}
function rgbToHex(r, g, b) {
  const h = (n) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
