import { useCallback, useEffect, useState } from "react";
import { ApiError, createItinerary, getItinerary, removeStop, swapStop, undoEdit } from "./api/client";
import DayLengthChips from "./components/DayLengthChips";
import DriveTimeChips from "./components/DriveTimeChips";
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
    <div dir="rtl" lang="he" className="min-h-screen bg-slate-50 text-slate-900">
      <header className="mx-auto max-w-2xl px-4 pt-8 pb-2 md:max-w-6xl md:px-8">
        <h1 className="text-2xl font-bold md:text-3xl">מסלול יום בישראל</h1>
      </header>

      <main className="mx-auto flex max-w-2xl flex-col gap-6 px-4 pb-24 md:max-w-6xl md:px-8">
        {status === "loading" && !itinerary && <p className="text-center text-slate-500">בונים מסלול…</p>}

        {status === "error" && errorText && (
          <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-700">
            {errorText}
          </p>
        )}

        {day && day.stops.length > 0 && (
          <Timeline day={day} onRemove={handleRemove} onSwap={handleSwap} disabled={busy} />
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

        <div className="flex flex-col gap-4 rounded-xl bg-white p-4 shadow-sm">
          <h2 className="text-lg font-semibold">רוצים משהו אחר?</h2>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600">אזור</p>
            <RegionChips selected={region} onSelect={handleRegionSelect} disabled={busy} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600">זמן נסיעה מקסימלי בין עצירות</p>
            <DriveTimeChips selected={maxLegMin} onSelect={handleMaxLegSelect} disabled={busy} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600">ארוחה</p>
            <MealChips selected={withMeal} onSelect={handleMealSelect} disabled={busy} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600">אורך היום</p>
            <DayLengthChips selected={dayLength} onSelect={handleDayLengthSelect} disabled={busy} />
          </div>
          <div>
            <p className="mb-2 text-sm font-medium text-slate-600">שמירת שבת</p>
            <ShabbatChips selected={shabbatObservant} onSelect={handleShabbatSelect} disabled={busy} />
            {getTodayWeekday() !== "fri" && (
              <p className="mt-1 text-xs text-slate-500">משפיע על תכנון ליום שישי בלבד</p>
            )}
          </div>
        </div>
      </main>

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
