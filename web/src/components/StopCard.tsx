import type { Stop } from "../types";

interface StopCardProps {
  stop: Stop;
  index: number;
  onRemove: (placeId: string) => void;
  onSwap: (placeId: string) => void;
  disabled?: boolean;
}

// Every field here is read straight off the Stop the API returned — no
// arithmetic, no derived end time. The API contract's own rule: "the client
// never does time arithmetic."
export default function StopCard({ stop, index, onRemove, onSwap, disabled = false }: StopCardProps) {
  const { place } = stop;
  const isMeal = place.category === "meal";

  return (
    <li
      className={
        "flex flex-col gap-3 rounded-xl border p-4 shadow-sm " +
        (isMeal ? "border-amber-300 bg-amber-50" : "border-slate-200 bg-white")
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-slate-500">עצירה {index + 1}</p>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-semibold text-slate-900">{place.name_he}</h3>
            {isMeal && (
              <span className="rounded-full bg-amber-200 px-2 py-0.5 text-xs font-medium text-amber-900">
                ארוחה
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          aria-label={`הסר את ${place.name_he} מהמסלול`}
          onClick={() => onRemove(stop.place_id)}
          disabled={disabled}
          className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-slate-300 text-slate-500 transition-colors hover:border-red-400 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <span aria-hidden="true">✕</span>
        </button>
      </div>

      {stop.travel_min_from_prev > 0 && (
        <p className="text-sm text-slate-500">{stop.travel_min_from_prev} דק׳ נסיעה מהעצירה הקודמת</p>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-600">
        <span>מגיעים בשעה {stop.arrive_at}</span>
        <span>משך ביקור: {stop.duration_min} דק׳</span>
      </div>

      <button
        type="button"
        onClick={() => onSwap(stop.place_id)}
        disabled={disabled}
        className="self-start rounded-full border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:border-emerald-400 hover:text-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
      >
        החלף עצירה
      </button>
    </li>
  );
}
