import { useCallback, useEffect, useState } from "react";
import { ApiError, createItinerary, getItinerary, removeStop, swapStop, undoEdit } from "./api/client";
import DayLengthChips from "./components/DayLengthChips";
import DriveTimeChips from "./components/DriveTimeChips";
import MapPanel from "./components/MapPanel";
import MealChips from "./components/MealChips";
import RegionChips from "./components/RegionChips";
import ShabbatChips from "./components/ShabbatChips";
import Timeline from "./components/Timeline";
import Toast from "./components/Toast";
import { clearStoredItineraryId, getOrCreateSessionId, getStoredItineraryId, setStoredItineraryId } from "./session";
import type { DayLength, Itinerary, MaxLegMin, Region } from "./types";
import { getTodayWeekday } from "./weekday";

type Status = "loading" | "ready" | "error";

interface ToastState {
  message: string;
  actionLabel?: string;
  onAction?: () => void;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "משהו השתבש. נסו שוב.";
}

function App() {
  // Pre-selected defaults so chips render on first paint even before any
  // network call resolves (US-1: "I never see an empty screen").
  const [region, setRegion] = useState<Region>("central");
  const [maxLegMin, setMaxLegMin] = useState<MaxLegMin>(45);
  const [withMeal, setWithMeal] = useState<boolean>(true);
  const [dayLength, setDayLength] = useState<DayLength>("long");
  const [shabbatObservant, setShabbatObservant] = useState<boolean>(true);
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  // Shared between Timeline (hover a card) and MapPanel (click a pin) —
  // lives here because the two are siblings, not because App has any use
  // for it itself.
  const [highlightedPlaceId, setHighlightedPlaceId] = useState<string | null>(null);

  const planNew = useCallback(
    async (
      nextRegion: Region,
      nextMaxLeg: MaxLegMin,
      nextWithMeal: boolean,
      nextDayLength: DayLength,
      nextShabbatObservant: boolean,
    ) => {
      setBusy(true);
      setStatus("loading");
      setErrorText(null);
      try {
        const created = await createItinerary({
          session_id: getOrCreateSessionId(),
          region: nextRegion,
          max_leg_min: nextMaxLeg,
          weekday: getTodayWeekday(),
          with_meal: nextWithMeal,
          day_length: nextDayLength,
          shabbat_observant: nextShabbatObservant,
        });
        setStoredItineraryId(created.id);
        setItinerary(created);
        setRegion(created.region);
        setMaxLegMin(created.max_leg_min);
        setStatus("ready");
        if (created.relaxed_to) {
          setToast({
            message: `הרחבנו את זמן הנסיעה ל-${created.relaxed_to} דקות כדי למצוא מספיק מקומות`,
          });
        }
      } catch (err) {
        setStatus("error");
        setErrorText(errorMessage(err));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  // US-3: resume on load if a saved itinerary exists; a 404 means the
  // in-memory server store lost it (e.g. a restart), so fall back to
  // planning fresh rather than showing an error for something the user
  // never caused.
  useEffect(() => {
    const savedId = getStoredItineraryId();
    if (!savedId) {
      void planNew(region, maxLegMin, withMeal, dayLength, shabbatObservant);
      return;
    }
    getItinerary(savedId)
      .then((resumed) => {
        setItinerary(resumed);
        setRegion(resumed.region);
        setMaxLegMin(resumed.max_leg_min);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 404) {
          clearStoredItineraryId();
          void planNew(region, maxLegMin, withMeal, dayLength, shabbatObservant);
        } else {
          setStatus("error");
          setErrorText(errorMessage(err));
        }
      });
    // Runs once on mount only — planNew and every chip state intentionally
    // excluded so a resumed itinerary isn't immediately replanned.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleRegionSelect(next: Region) {
    if (busy || next === region) return;
    setRegion(next);
    void planNew(next, maxLegMin, withMeal, dayLength, shabbatObservant);
  }

  function handleMaxLegSelect(next: MaxLegMin) {
    if (busy || next === maxLegMin) return;
    setMaxLegMin(next);
    void planNew(region, next, withMeal, dayLength, shabbatObservant);
  }

  function handleMealSelect(next: boolean) {
    if (busy || next === withMeal) return;
    setWithMeal(next);
    void planNew(region, maxLegMin, next, dayLength, shabbatObservant);
  }

  function handleDayLengthSelect(next: DayLength) {
    if (busy || next === dayLength) return;
    setDayLength(next);
    void planNew(region, maxLegMin, withMeal, next, shabbatObservant);
  }

  function handleShabbatSelect(next: boolean) {
    if (busy || next === shabbatObservant) return;
    setShabbatObservant(next);
    void planNew(region, maxLegMin, withMeal, dayLength, next);
  }

  async function handleUndo() {
    if (!itinerary || busy) return;
    setBusy(true);
    try {
      setItinerary(await undoEdit(itinerary.id));
    } catch (err) {
      setToast({ message: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(placeId: string) {
    if (!itinerary || busy) return;
    const removedPlace = itinerary.days[0]?.stops.find((stop) => stop.place_id === placeId)?.place;
    setBusy(true);
    try {
      setItinerary(await removeStop(itinerary.id, placeId));
      setToast({
        message: removedPlace ? `${removedPlace.name_he} הוסר/ה מהמסלול` : "העצירה הוסרה מהמסלול",
        actionLabel: "בטל",
        onAction: () => void handleUndo(),
      });
    } catch (err) {
      setToast({ message: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function handleSwap(placeId: string) {
    if (!itinerary || busy) return;
    setBusy(true);
    try {
      setItinerary(await swapStop(itinerary.id, placeId));
    } catch (err) {
      setToast({ message: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  const day = itinerary?.days[0];
  const hasFallbackPlaces = itinerary !== null && itinerary.days.length === 0 && itinerary.places.length > 0;
  const wantedMealButNoneFound =
    withMeal && itinerary !== null && day !== undefined && day.stops.length > 0 && !itinerary.meal_included;
  const dayLengthNotMatched =
    itinerary !== null && day !== undefined && day.stops.length > 0 && !itinerary.length_matched;

  return (
    <div dir="rtl" lang="he" className="min-h-screen bg-page text-slate-900">
      {/* Full-bleed solid band — bg-emerald-800, not the chips' own
          emerald-600: white text on emerald-600 measures only 3.77:1,
          under WCAG AA's 4.5:1 floor, and the brief is explicit that the
          fix is a darker band, never a dimmer white. emerald-700 (one step
          down) still only gets the *title* to 5.48:1 — the dimmed
          subtitle at white/70% on top of emerald-700 measures 3.45:1,
          still failing. emerald-800 is what actually clears both: title
          7.6:1, subtitle 4.59:1 at white/70%. Bumped to white/75% anyway
          for a bit more margin over the floor (measures ~5:1) rather than
          sitting right on the line. Both stay the app's one green family
          (Tailwind's own tokens, not a new hex). Height 160px / 200px
          (md:) is the requested 160-200px range — shorter than the old
          photo band needed, since a flat fill carries no unusable dead
          space. The subtitle uses whitespace-nowrap and steps down in
          size at md: specifically so it can never wrap to a third line at
          narrow widths — verified at 400px. h-[120px] base (was 160px) is
          the phone-pass shrink; md:h-[200px] (>=768px) is untouched. */}
      <header className="flex h-[120px] flex-col items-center justify-center gap-1 bg-emerald-800 px-4 text-center md:h-[200px] md:gap-3">
        <h1 className="text-2xl font-bold text-white md:text-[3rem]">מסלול יום בישראל</h1>
        <p className="whitespace-nowrap text-xs text-white/75 md:text-lg">בוחרים אזור, מקבלים יום מתוכנן</p>
      </header>

      {/* Two nested rows, not one, so the sidebar/itinerary split and the
          map's own join point can sit at different breakpoints. First
          measured this as a single 900px row for all three: sidebar
          (fixed 400px) + map placeholder + gaps/padding left the
          itinerary ~150px wide at exactly 900px — a place name wrapped
          across 4 lines. That's not an edge case worth tolerating, so the
          map now joins as a column starting at 1200px; 900-1199px keeps
          exactly the sidebar+itinerary 2-column layout that already
          existed (and was already validated) before this change, with
          the map sitting as a normal full-width block below the
          itinerary — same treatment as <900px, just covering a wider
          range. outerRow (this element) controls the map's join point;
          innerRow (sidebar+main, right below) controls the older,
          untouched 900px sidebar breakpoint.

          Card-width pass: with the sidebar fixed at 400px (tuned earlier
          for its own chip row) and main already flex-1 (i.e. already
          claiming every pixel not spoken for), the only way to widen the
          itinerary is to shrink what IS spoken for — outer gap-10 -> gap-6
          and the map's own 260px -> 220px at the 1200px tier, its jump to
          a roomier width pushed from 1500px out to 1800px. Edge padding
          (min-[900px]:px-12) is left alone — that was a deliberate
          "comfortable margin from the screen edge" decision from an
          earlier pass, not something this request touches. */}
      <div className="mx-auto flex max-w-2xl flex-col gap-6 px-4 pb-24 md:px-8 min-[768px]:max-w-[1152px] min-[900px]:max-w-[1400px] min-[900px]:px-12 min-[1200px]:max-w-[1800px] min-[1200px]:flex-row min-[1200px]:items-start min-[1200px]:gap-6">
        <div className="flex min-w-0 flex-1 flex-col gap-6 min-[900px]:flex-row min-[900px]:items-start min-[900px]:gap-16">
          {/* md:text-[1.0625rem] here is the one base bump for the whole
              sidebar: every text size below it (h2/labels/chips/note) is an
              em value, i.e. a multiple of THIS element's font-size, not an
              independent rem value of its own. Tailwind's own text-*
              utilities are deliberately rem-based (root-relative, so
              nesting never silently compounds them) — which is exactly why
              they can't be the mechanism here: a child's rem class ignores
              an ancestor's font-size entirely. em is the one unit that
              actually cascades from a single parent bump, so it is used
              here instead, only for this subtree. */}
          <aside className="flex flex-none flex-col gap-4 rounded-xl bg-white p-4 shadow-sm min-[900px]:sticky min-[900px]:top-8 min-[900px]:w-[400px] min-[900px]:px-3 md:text-[1.0625rem]">
          <h2 className="text-lg font-semibold md:text-[1.3em]">רוצים משהו אחר?</h2>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600 md:text-[1em]">אזור</p>
            <RegionChips selected={region} onSelect={handleRegionSelect} disabled={busy} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600 md:text-[1em]">זמן נסיעה מקסימלי בין עצירות</p>
            <DriveTimeChips selected={maxLegMin} onSelect={handleMaxLegSelect} disabled={busy} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600 md:text-[1em]">ארוחה</p>
            <MealChips selected={withMeal} onSelect={handleMealSelect} disabled={busy} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600 md:text-[1em]">אורך היום</p>
            <DayLengthChips selected={dayLength} onSelect={handleDayLengthSelect} disabled={busy} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600 md:text-[1em]">שמירת שבת</p>
            <ShabbatChips selected={shabbatObservant} onSelect={handleShabbatSelect} disabled={busy} />
            {getTodayWeekday() !== "fri" && (
              <p className="mt-1 text-xs text-slate-500 md:text-[0.8em]">משפיע על תכנון ליום שישי בלבד</p>
            )}
          </div>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col gap-6">
          {status === "loading" && !itinerary && <p className="text-center text-slate-500">בונים מסלול…</p>}

          {status === "error" && errorText && (
            <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">
              {errorText}
            </p>
          )}

          {day && day.stops.length > 0 && (
            <Timeline
              day={day}
              onRemove={handleRemove}
              onSwap={handleSwap}
              disabled={busy}
              highlightedPlaceId={highlightedPlaceId}
              onHighlight={setHighlightedPlaceId}
            />
          )}

          {wantedMealButNoneFound && (
            <p className="text-sm text-slate-500">לא נמצאה מסעדה מתאימה במרחק הנסיעה שנבחר</p>
          )}

          {dayLengthNotMatched && (
            <p className="text-sm text-slate-500">
              {shabbatObservant && getTodayWeekday() === "fri"
                ? "היום מסתיים ב-15:00 לקראת שבת, ולכן קצר מהמבוקש"
                : "משך היום יצא קצר יותר מהמבוקש"}
            </p>
          )}

          {hasFallbackPlaces && itinerary && (
            <div className="flex flex-col gap-3">
              <p role="status" className="rounded-xl bg-amber-50 p-4 text-amber-900">
                לא מצאנו מספיק מקומות לבנות מסלול מלא באזור הזה עם ההגבלות הנוכחיות. הנה מה שיש:
              </p>
              <ul className="flex flex-col gap-3">
                {itinerary.places.map((place) => (
                  <li key={place.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                    <h3 className="text-lg font-semibold">{place.name_he}</h3>
                    {place.description_he && <p className="mt-1 text-sm text-slate-600">{place.description_he}</p>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </main>
        </div>

        {/* Map column. Below 1200px this is a normal full-width block
            (fixed ~300px height) sitting after the sidebar+itinerary row
            in document order — never covering the list, never a
            fullscreen overlay. At 1200px+ it becomes the sticky third
            column, visually the leftmost (inline-end) since it's last in
            document order under dir="rtl". Width there is fluid (220px at
            1200px, up to 380px at 1800px+): a flat 400px next to the
            sidebar's own fixed 400px is what caused the original 900px
            squeeze worked out earlier, and the same arithmetic bites
            again at any single fixed width chosen too close to its own
            breakpoint — kept narrower for longer this pass specifically
            to give the itinerary column more room in the commonly-tested
            1200-1799px range. day is only defined once an itinerary with a real
            day has loaded — MapPanel still renders with an empty stops
            array before that (a static Israel-wide view, no markers)
            rather than popping the sticky panel in and out of existence. */}
        <div className="h-[300px] w-full shrink-0 overflow-hidden rounded-xl border border-slate-200 bg-white p-2 shadow-sm min-[1200px]:sticky min-[1200px]:top-8 min-[1200px]:h-[600px] min-[1200px]:w-[220px] min-[1800px]:w-[380px]">
          <MapPanel
            stops={day?.stops ?? []}
            highlightedPlaceId={highlightedPlaceId}
            onPinClick={setHighlightedPlaceId}
          />
        </div>
      </div>

      {toast && (
        <Toast
          message={toast.message}
          actionLabel={toast.actionLabel}
          onAction={toast.onAction}
          onDismiss={() => setToast(null)}
        />
      )}
    </div>
  );
}

export default App;
