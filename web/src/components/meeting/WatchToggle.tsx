import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "../Icon";
import { api } from "../../lib/api";
import { qk } from "../../lib/queries";

export function WatchToggle({ meetingId }: { meetingId: number }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: qk.watch(meetingId),
    queryFn: () => api.isWatching(meetingId),
  });
  const watching = data?.watching ?? false;
  const toggle = useMutation({
    mutationFn: () =>
      watching ? api.unwatchMeeting(meetingId) : api.watchMeeting(meetingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.watch(meetingId) });
    },
  });
  return (
    <button
      className="btn btn-sm"
      onClick={() => toggle.mutate()}
      disabled={toggle.isPending}
      title={
        watching
          ? "Stop watching — you won't get notifications about this meeting."
          : "Watch — get a notification when this briefing is approved."
      }
    >
      <Icon name={watching ? "eye-off" : "eye"} size={12} />{" "}
      {watching ? "Watching" : "Watch"}
    </button>
  );
}
