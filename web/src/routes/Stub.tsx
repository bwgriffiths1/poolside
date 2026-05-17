import { useNavigate } from "react-router-dom";
import { Topbar } from "../components/Topbar";

const LABELS: Record<string, string> = {
  deepdive: "Deep Dive",
  bulk: "Bulk Summarize",
  prompts: "Prompt Library",
  settings: "Settings",
};

export function Stub({ name }: { name: string }) {
  const navigate = useNavigate();
  const label = LABELS[name] ?? name;
  return (
    <>
      <Topbar crumbs={[{ label }]} />
      <div className="page">
        <div className="page-header">
          <div className="page-eyebrow">Forthcoming</div>
          <h1 className="page-title">{label}</h1>
          <p className="page-subtitle">
            Forthcoming — this screen isn't built yet.
          </p>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn btn-primary" onClick={() => navigate("/overview")}>
            ← Back to Overview
          </button>
          <button className="btn" onClick={() => navigate("/briefings")}>
            Browse briefings
          </button>
        </div>
      </div>
    </>
  );
}
