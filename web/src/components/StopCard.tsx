import type { Place, PlaceImage, Stop } from "../types";

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
        "flex gap-3 rounded-xl border p-4 shadow-sm " +
        (isMeal ? "border-amber-300 bg-amber-50" : "border-slate-200 bg-white")
      }
    >
      {/* The text column is first in document order, the images block second —
          in a flex row under dir="rtl" that alone puts images at the
          inline-end (visually left), no ml-/mr-/left- needed. */}
      <div className="flex flex-1 flex-col gap-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-medium text-slate-500">עצירה {index + 1}</p>
            <div className="flex flex-wrap items-center gap-2">
              <StopName place={place} />
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
      </div>

      <PlaceImages images={place.images} name={place.name_he} />
    </li>
  );
}

// Links the name to the place's official page when there is one. No anchor
// at all when official_url is absent — never a link to nowhere.
function StopName({ place }: { place: Place }) {
  if (!place.official_url) {
    return <h3 className="text-lg font-semibold text-slate-900">{place.name_he}</h3>;
  }
  return (
    <h3 className="text-lg font-semibold text-slate-900">
      <a
        href={place.official_url}
        target="_blank"
        rel="noopener noreferrer"
        className="underline decoration-slate-300 underline-offset-2 hover:decoration-slate-500"
      >
        {place.name_he}
      </a>
    </h3>
  );
}

// Up to 3 Commons photos: one ~96px hero, up to two smaller ones beneath it.
// No carousel, no lightbox, no arrows — every photo is a plain link to its
// own Commons file page, nothing hidden behind interaction. Empty images is
// the common case (most restaurants and small sites have no Commons
// coverage), so the fallback is a deliberate grey initial, not a broken icon.
function PlaceImages({ images, name }: { images: PlaceImage[]; name: string }) {
  if (images.length === 0) {
    return (
      <div
        aria-hidden="true"
        className="flex h-24 w-24 shrink-0 items-center justify-center rounded-lg bg-slate-200 text-2xl font-semibold text-slate-400"
      >
        {name.charAt(0)}
      </div>
    );
  }

  const [hero, ...rest] = images;

  return (
    <div className="flex shrink-0 flex-col items-center gap-1">
      <a href={hero.source_url} target="_blank" rel="noopener noreferrer">
        <img src={hero.url} alt={name} className="h-24 w-24 rounded-lg object-cover" />
      </a>
      {rest.length > 0 && (
        <div className="flex gap-1">
          {rest.map((image) => (
            <a key={image.url} href={image.source_url} target="_blank" rel="noopener noreferrer">
              <img src={image.url} alt={name} className="h-10 w-10 rounded-md object-cover" />
            </a>
          ))}
        </div>
      )}
      <a
        href={hero.source_url}
        target="_blank"
        rel="noopener noreferrer"
        title={hero.credit}
        className="max-w-24 truncate text-center text-[10px] text-slate-400 hover:text-slate-600"
      >
        {hero.credit}
      </a>
    </div>
  );
}
