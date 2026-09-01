import type { StartsAt } from "../types";

// Four options, one over the project's own max-3-chips-per-row convention
// (see RegionChips/DriveTimeChips/MealChips/DayLengthChips — all 2-3). Built
// as asked; flagged rather than quietly dropping 08:00 myself, since that's
// a product call, not an engineering one.
const OPTIONS: readonly StartsAt[] = ["08:00", "09:00", "10:00", "11:00"];

interface StartTimeChipsProps {
  selected: StartsAt;
  onSelect: (value: StartsAt) => void;
  disabled?: boolean;
}

export default function StartTimeChips({ selected, onSelect, disabled = false }: StartTimeChipsProps) {
  return (
    <div role="radiogroup" aria-label="שעת יציאה" className="flex flex-wrap gap-2">
      {OPTIONS.map((time) => {
        const isSelected = time === selected;
        return (
          <button
            key={time}
            type="button"
            role="radio"
            aria-checked={isSelected}
            disabled={disabled}
            onClick={() => onSelect(time)}
            className={
              "rounded-full border px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 " +
              (isSelected
                ? "border-emerald-600 bg-emerald-600 text-white"
                : "border-slate-300 bg-white text-slate-700 hover:border-emerald-400")
            }
          >
            {time}
          </button>
        );
      })}
    </div>
  );
}
