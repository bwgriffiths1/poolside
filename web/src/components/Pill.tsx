import type { MeetingStatus } from "../types";

const LABELS: Record<MeetingStatus, string> = {
  scheduled: "Scheduled",
  materials: "Materials Posted",
  summarized: "Summarized",
  updated: "Updated",
};

interface PillProps {
  status: MeetingStatus | "complete";
  label?: string;
}

export function Pill({ status, label }: PillProps) {
  const cls = status === "complete" ? "summarized" : status;
  const text = label ?? (status === "complete" ? "Complete" : LABELS[status as MeetingStatus]);
  return (
    <span className={`pill ${cls}`}>
      <span className="dot" />
      {text}
    </span>
  );
}
