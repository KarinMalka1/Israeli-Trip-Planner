import type { MaxLegMin } from "../types";

const OPTIONS: readonly MaxLegMin[] = [20, 45, 90];

interface DriveTimeChipsProps {
  selected: MaxLegMin;
  onSelect: (value: MaxLegMin) => void;
  disabled?: boolean;
}

export default function DriveTimeChips({ selected, onSelect, disabled = false }: DriveTimeChipsProps) {
  return (
    <div role="radiogroup" aria-label="זמן נסיעה מקסימלי בין עצירות" className="flex flex-wrap gap-2">
      {OPTIONS.map((minutes) => {
        const isSelected = minutes === selected;
        return (
          <button
            key={minutes}
            type="button"
            role="radio"
            aria-checked={isSelected}
            disabled={disabled}
            onClick={() => onSelect(minutes)}
            className={
              "rounded-full border px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 " +
              (isSelected
                ? "border-emerald-600 bg-emerald-600 text-white"
                : "border-slate-300 bg-white text-slate-700 hover:border-emerald-400")
            }
          >
            {`עד ${minutes} דק׳`}
          </button>
        );
      })}
    </div>
  );
}
