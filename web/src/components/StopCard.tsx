import { forwardRef, useEffect, useRef, useState } from "react";
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
        "flex flex-col items-stretch gap-6 rounded-xl border p-8 shadow-sm md:flex-row md:gap-12 md:p-16 " +
        (isMeal ? "border-amber-300 bg-amber-50" : "border-emerald-600 bg-white")
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
          The gap here (name/times/button spacing) plus the card's own
          padding above are what roughly double the card's height — same
          content, no new fields, just room to breathe. */}
      <div className="flex min-w-0 flex-col gap-6 md:w-2/3 md:flex-none md:gap-11">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-medium text-slate-500 md:text-sm">עצירה {index + 1}</p>
            <div className="flex flex-wrap items-center gap-2">
              <StopName place={place} />
              {isMeal && (
                <span className="rounded-full bg-amber-200 px-2 py-0.5 text-xs font-medium text-amber-900 md:text-3xl">
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
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-slate-300 text-slate-500 transition-colors hover:border-red-400 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-60 md:h-11 md:w-11"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>

        {stop.travel_min_from_prev > 0 && (
          <p className="text-sm text-slate-500 md:text-base">{stop.travel_min_from_prev} דק׳ נסיעה מהעצירה הקודמת</p>
        )}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-base text-slate-600 md:text-lg">
          <span>מגיעים בשעה {stop.arrive_at}</span>
          <span>משך ביקור: {stop.duration_min} דק׳</span>
        </div>

        <button
          type="button"
          onClick={() => onSwap(stop.place_id)}
          disabled={disabled}
          className="self-start rounded-full border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:border-emerald-400 hover:text-emerald-700 disabled:cursor-not-allowed disabled:opacity-60 md:px-4 md:py-2.5 md:text-base"
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
// hover underline differs.
function StopName({ place }: { place: Place }) {
  const headingClass = "text-lg font-semibold text-slate-900 md:text-4xl";
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

// Shared by both PlaceImages branches so the card's width — and, at md:,
// its height — never depends on whether the place has images: an
// aspect-ratio box below md: (there is no sibling to stretch to yet), full
// height matched to the text column at and above it via the <li>'s
// items-stretch — deliberately NOT an explicit h-full here too: a flex
// item's stretched cross size is definite for its own children's
// flex-basis/flex-grow resolution, but layering a redundant height:100% on
// top of that pushed it back into an indefinite-height context in testing,
// silently breaking the hero:small-row 3:1 ratio below (confirmed by
// removing it and watching the ratio resolve correctly).
const IMAGE_COLUMN_CLASS = "aspect-[16/9] w-full md:aspect-auto md:w-1/3 md:flex-none";

// One large hero, up to two smaller ones beneath it, side by side. Clicking
// a small image swaps it into the hero slot (pure local state — no API
// call, no URL change); nothing is ever hidden, so this isn't a carousel.
// The hero:small-row height ratio (flex-[3]:flex-[2], ~60%/40%) fills
// whatever height the column ends up with rather than a fixed px, so this
// scales with the text column instead of imposing its own height on the
// card. Empty images is the common case (most restaurants and small sites
// have no Commons coverage), so the fallback is a deliberate grey initial
// filling the same column, not a broken icon.
function PlaceImages({ images, name }: { images: PlaceImage[]; name: string }) {
  const [featuredIndex, setFeaturedIndex] = useState(0);
  // Swapping moves a button from the small row into the hero slot, a
  // different position in the tree, so React remounts it and the browser
  // drops focus to <body>. Refocus the new hero button after a user-driven
  // swap so keyboard users don't lose their place — but not on first mount,
  // where nothing has been interacted with yet.
  const heroRef = useRef<HTMLButtonElement>(null);
  const swappedByUser = useRef(false);

  useEffect(() => {
    if (swappedByUser.current) heroRef.current?.focus();
  }, [featuredIndex]);

  function handleSelect(index: number) {
    swappedByUser.current = true;
    setFeaturedIndex(index);
  }

  if (images.length === 0) {
    return (
      <div className={IMAGE_COLUMN_CLASS}>
        <div
          aria-hidden="true"
          className="flex h-full w-full items-center justify-center rounded-lg bg-slate-200 text-3xl font-semibold text-slate-400"
        >
          {name.charAt(0)}
        </div>
      </div>
    );
  }

  const hero = images[featuredIndex];
  const smallIndices = images.map((_, i) => i).filter((i) => i !== featuredIndex);

  return (
    <div className={`${IMAGE_COLUMN_CLASS} flex flex-col gap-1`}>
      <ImageButton
        ref={heroRef}
        image={hero}
        index={featuredIndex}
        total={images.length}
        name={name}
        rounded="rounded-lg"
        onSelect={handleSelect}
        className="min-h-0 flex-[3]"
      />
      {smallIndices.length > 0 && (
        <div className="flex min-h-0 flex-[2] gap-1">
          {smallIndices.map((i) => (
            <ImageButton
              key={images[i].url}
              image={images[i]}
              index={i}
              total={images.length}
              name={name}
              rounded="rounded-md"
              onSelect={handleSelect}
              className="h-full min-h-0 min-w-0 flex-1"
            />
          ))}
        </div>
      )}
      <a
        href={hero.source_url}
        target="_blank"
        rel="noopener noreferrer"
        title={hero.credit}
        className="shrink-0 truncate text-center text-[10px] text-slate-400 hover:text-slate-600 md:text-3xl"
      >
        {hero.credit}
      </a>
    </div>
  );
}

// index/total refer to the image's fixed position within the place's own
// images array, not its current slot — so "תמונה 2 מתוך 3" keeps naming the
// same photo whether it's currently featured or small.
//
// The <img> is position:absolute + inset-0, not a normal-flow flex child of
// the button. An in-flow <img> with h-full/object-cover still carries its
// own real intrinsic aspect ratio, and when this button's own height comes
// from flex-grow inside a column (PlaceImages) whose height is itself only
// known via items-stretch from a sibling, browsers fall back to that
// intrinsic ratio while computing the column's un-stretched auto height —
// confirmed by removing every <img> in that state and watching the whole
// column collapse toward zero. Taking the <img> out of flow removes it from
// that auto-size computation entirely, which is what lets the image column
// actually follow the text column's height instead of the reverse.
const ImageButton = forwardRef<
  HTMLButtonElement,
  {
    image: PlaceImage;
    index: number;
    total: number;
    name: string;
    rounded: string;
    onSelect: (index: number) => void;
    className: string;
  }
>(function ImageButton({ image, index, total, name, rounded, onSelect, className }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      onClick={() => onSelect(index)}
      aria-label={`תמונה ${index + 1} מתוך ${total}`}
      className={
        `${className} ${rounded} relative block w-full overflow-hidden border-0 bg-transparent p-0 focus-visible:outline ` +
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
      }
    >
      <img src={image.url} alt={name} className={`absolute inset-0 h-full w-full object-cover ${rounded}`} />
    </button>
  );
});
