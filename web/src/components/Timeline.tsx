import type { Day } from "../types";
import StopCard from "./StopCard";

interface TimelineProps {
  day: Day;
  onRemove: (placeId: string) => void;
  onSwap: (placeId: string) => void;
  disabled?: boolean;
}

// Renders days[0].stops in order. starts_at/ends_at are shown as-is from the
// Day the API returned — ends_at is the "cumulative ends_at" that updates on
// every remove/swap because App re-renders from the full recomputed
// Itinerary, never because this component computed anything.
export default function Timeline({ day, onRemove, onSwap, disabled = false }: TimelineProps) {
  return (
    <section aria-label="לוח הזמנים של היום" className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-xl bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-900">
        <span>יוצאים בשעה {day.starts_at}</span>
        <span>סיום משוער בשעה {day.ends_at}</span>
      </div>

      <ol className="flex flex-col gap-4">
        {day.stops.map((stop, index) => (
          <StopCard
            key={stop.place_id}
            stop={stop}
            index={index}
            onRemove={onRemove}
            onSwap={onSwap}
            disabled={disabled}
          />
        ))}
      </ol>
    </section>
  );
}
