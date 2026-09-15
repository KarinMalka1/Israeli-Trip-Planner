import { useRef, useState } from "react";
import Lightbox from "./Lightbox";
import type { Place, PlaceImage, Stop } from "../types";

interface StopCardProps {
  stop: Stop;
  index: number;
  onRemove: (placeId: string) => void;
  onSwap: (placeId: string) => void;
  disabled?: boolean;
  // The map (a sibling component up in App) is the other half of this
  // shared highlight state — hovering this card tells the map which pin
  // to recolour; isHighlighted is how a pin *click* reflects back here.
  // Both optional: StopCard renders correctly with no map at all.
  isHighlighted?: boolean;
  onHighlight?: (placeId: string | null) => void;
}

// Every field here is read straight off the Stop the API returned — no
// arithmetic, no derived end time. The API contract's own rule: "the client
// never does time arithmetic."
// Hebrew explanations for the two disabled-button states — shown as both
// title and aria-label so the reason is available on hover and to screen
// readers alike. can_remove/can_swap come from the API already computed
// (SPEC section 7); this component only chooses which string to show.
const CANNOT_REMOVE_LABEL = "לא ניתן להסיר — מסלול חייב לפחות 3 עצירות";
const CANNOT_SWAP_LABEL = "לא ניתן להחליף — לא נמצאה עצירה חלופית במרחק הנסיעה שנבחר";

export default function StopCard({
  stop,
  index,
  onRemove,
  onSwap,
  disabled = false,
  isHighlighted = false,
  onHighlight,
}: StopCardProps) {
  const { place } = stop;
  const isMeal = place.category === "meal";

  return (
    <li
      onMouseEnter={() => onHighlight?.(stop.place_id)}
      onMouseLeave={() => onHighlight?.(null)}
      className={
        "flex flex-col items-stretch gap-4 rounded-xl border p-4 shadow-sm transition-shadow md:flex-row md:gap-12 md:p-16 " +
        (isMeal ? "border-amber-300 bg-amber-50" : "border-emerald-600 bg-white") +
        (isHighlighted ? " ring-2 ring-red-500 ring-offset-2" : "")
      }
    >
      {/* The text column is first in document order, the images block second —
          in a flex row under dir="rtl" that alone puts text at the inline-start
          (visually right) and images at the inline-end (visually left), no
          ml-/mr-/left- anywhere. Below 768px (Tailwind's md: breakpoint, used
          throughout this card and in App.tsx's own page-width/type-scale
          switch so everything moves together) the row becomes a column, so
          the same document order stacks text above images.
          items-stretch (the flex default, stated explicitly here since it's
          load-bearing) is what makes the image column's height follow the
          text column's height at md:, rather than a fixed px square.
          The card's own p-8/gap-6 (desktop, via md:) is what roughly
          doubles the card's height there — same content, no new fields,
          just room to breathe. That spacing was tuned for desktop and
          unprefixed, so it was also landing on phones; p-4/gap-4 here is
          the phone-appropriate base, scoped below md: only now. */}
      <div className="flex min-w-0 flex-col gap-3 md:w-3/5 md:flex-none md:gap-11">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-medium text-slate-500 md:text-sm">עצירה {index + 1}</p>
            <div className="flex flex-wrap items-center gap-2">
              <StopName place={place} />
              {isMeal && (
                <span className="rounded-full bg-amber-200 px-2 py-0.5 text-xs font-medium text-amber-900 md:text-sm">
                  ארוחה
                </span>
              )}
            </div>
          </div>
          <button
            type="button"
            aria-label={stop.can_remove ? `הסר את ${place.name_he} מהמסלול` : CANNOT_REMOVE_LABEL}
            title={stop.can_remove ? undefined : CANNOT_REMOVE_LABEL}
            onClick={() => onRemove(stop.place_id)}
            disabled={disabled || !stop.can_remove}
            className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-slate-300 text-slate-500 transition-colors hover:border-red-400 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>

        <div className="flex flex-col gap-1">
          {stop.travel_min_from_prev > 0 && (
            <p className="text-sm leading-snug text-slate-500 md:text-lg">{stop.travel_min_from_prev} דק׳ נסיעה מהעצירה הקודמת</p>
          )}

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-base leading-snug text-slate-600 md:text-lg">
            <span>מגיעים בשעה {stop.arrive_at}</span>
            <span>משך ביקור: {stop.duration_min} דק׳</span>
          </div>
        </div>

        <button
          type="button"
          aria-label={stop.can_swap ? undefined : CANNOT_SWAP_LABEL}
          title={stop.can_swap ? undefined : CANNOT_SWAP_LABEL}
          onClick={() => onSwap(stop.place_id)}
          disabled={disabled || !stop.can_swap}
          className="min-h-11 self-start rounded-full border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-700 transition-colors hover:border-emerald-400 hover:text-emerald-700 disabled:cursor-not-allowed disabled:opacity-60 md:px-4 md:py-2.5 md:text-base"
        >
          החלף עצירה
        </button>
      </div>

      <PlaceImages images={place.images} name={place.name_he} />
    </li>
  );
}

// Links the name to the place's official page when there is one. No anchor
// at all when official_url is absent — never a link to nowhere. Both
// branches share the exact same h3 className (weight, colour, size) so the
// heading never changes size depending on whether a link exists — only the
// hover underline differs. text-base (was text-lg) at base: at text-lg a
// long name plus the card's narrower phone width wrapped to 3 lines;
// text-base plus the phone padding fix above keeps it to 1-2. md:text-4xl
// (desktop) is untouched.
function StopName({ place }: { place: Place }) {
  const headingClass = "text-base font-semibold text-slate-900 md:text-4xl";
  if (!place.official_url) {
    return <h3 className={headingClass}>{place.name_he}</h3>;
  }
  return (
    <h3 className={headingClass}>
      <a
        href={place.official_url}
        target="_blank"
        rel="noopener noreferrer"
        className="underline-offset-2 hover:underline hover:decoration-slate-400"
      >
        {place.name_he}
      </a>
    </h3>
  );
}

// Shared by both PlaceImages branches (image present vs. empty/failed) so
// the card's width — and, at md:, its height — never depends on which one
// renders: an aspect-ratio box below md: (there is no sibling to stretch to
// yet), full height matched to the text column at and above it via the
// <li>'s items-stretch — deliberately NOT an explicit h-full here too: a
// flex item's stretched cross size is definite for its own children's
// flex-basis/flex-grow resolution, but layering a redundant height:100% on
// top of that pushed it back into an indefinite-height context in testing
// (confirmed by removing it and watching the column's own children resolve
// correctly again). md:w-2/5 (~40%) pairs with the text column's md:w-3/5.
const IMAGE_COLUMN_CLASS = "aspect-[16/9] w-full md:aspect-auto md:w-2/5 md:flex-none";

const EMPTY_IMAGE_PANEL = (name: string) => (
  <div className={IMAGE_COLUMN_CLASS}>
    <div
      aria-hidden="true"
      className="flex h-full w-full items-center justify-center rounded-lg bg-slate-200 text-3xl font-semibold text-slate-400"
    >
      {name.charAt(0)}
    </div>
  </div>
);

// The CARD itself is a single static image and no carousel — the
// thumbnail-swap gallery was removed earlier because almost no place in
// the dataset has 3 free-licence photos, so a row of thumbnails never had
// anything to show. images[0] is the only one ever rendered here; the
// full array is passed through to the Lightbox (below), which is the only
// place navigation between them exists. Empty images is the common case
// (most restaurants and small sites have no free photo at all), so the
// fallback is a deliberate grey initial filling the same column, not a
// broken icon — and the same fallback covers a place that does have an
// images entry but whose file 404s (a data/disk mismatch, not something
// the client can fix), via onError below, rather than showing the
// browser's own broken-image glyph. The fallback panel is a plain <div>,
// never a <button> — nothing to open, so it must not look or behave
// clickable.
function PlaceImages({ images, name }: { images: PlaceImage[]; name: string }) {
  const [failed, setFailed] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  if (images.length === 0 || failed) {
    return EMPTY_IMAGE_PANEL(name);
  }

  const image = images[0];

  return (
    <div className={`${IMAGE_COLUMN_CLASS} flex flex-col gap-1`}>
      {/* The <img> is position:absolute + inset-0, not a normal-flow flex
          child of this wrapper. An in-flow <img> with h-full/object-cover
          still carries its own real intrinsic aspect ratio, and when this
          wrapper's own height comes from flex-1 inside a column
          (PlaceImages) whose height is itself only known via the <li>'s
          items-stretch, browsers fall back to that intrinsic ratio while
          computing the column's un-stretched auto height — confirmed by
          removing the <img> in that state and watching the whole column
          collapse toward zero. Taking the <img> out of flow removes it
          from that auto-size computation entirely, which is what lets the
          image column actually follow the text column's height instead of
          the reverse. The wrapper is now a <button>, not a <div>: clicking
          (or Enter/Space, native to <button>) opens the Lightbox. Focus
          returns here on close via triggerRef, passed down as onClose's
          job rather than the Lightbox's — the Lightbox does not know or
          care what opened it. */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setLightboxOpen(true)}
        aria-label={
          images.length > 1
            ? `הגדלת התמונה של ${name} (תמונה 1 מתוך ${images.length})`
            : `הגדלת התמונה של ${name}`
        }
        className="relative block min-h-0 w-full flex-1 overflow-hidden rounded-lg border-0 bg-transparent p-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
      >
        <img
          src={image.url}
          alt={name}
          onError={() => setFailed(true)}
          className="absolute inset-0 h-full w-full object-cover rounded-lg"
        />
        {/* The count badge is what makes it acceptable that the card only
            ever shows images[0]: without it, images[1..] would be
            reachable only by guessing there's more behind a click, which
            is exactly the hidden content our accessibility rules forbid.
            The aria-label above says the same thing to screen-reader
            users, who would never see this visual badge at all. */}
        {images.length > 1 && (
          <span
            aria-hidden="true"
            className="absolute bottom-1.5 end-1.5 rounded-full bg-black/60 px-2 py-0.5 text-xs font-medium text-white"
          >
            {images.length}
          </span>
        )}
      </button>
      <a
        href={image.source_url}
        target="_blank"
        rel="noopener noreferrer"
        title={image.credit}
        className="shrink-0 truncate text-center text-[10px] text-slate-400 hover:text-slate-600 md:text-sm"
      >
        {image.credit}
      </a>
      {lightboxOpen && (
        <Lightbox
          images={images}
          name={name}
          onClose={() => {
            setLightboxOpen(false);
            triggerRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}
