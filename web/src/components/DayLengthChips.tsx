import type { DayLength } from "../types";

const OPTIONS: ReadonlyArray<{ value: DayLength; label: string }> = [
  { value: "short", label: "יום קצר" },
  { value: "long", label: "יום ארוך" },
];

interface DayLengthChipsProps {
  selected: DayLength;
  onSelect: (value: DayLength) => void;
  disabled?: boolean;
}

export default function DayLengthChips({ selected, onSelect, disabled = false }: DayLengthChipsProps) {
  return (
    <div role="radiogroup" aria-label="אורך היום" className="flex flex-wrap gap-2">
      {OPTIONS.map((option) => {
        const isSelected = option.value === selected;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={isSelected}
            disabled={disabled}
            onClick={() => onSelect(option.value)}
            className={
              "rounded-full border px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 md:text-base " +
              (isSelected
                ? "border-emerald-600 bg-emerald-600 text-white"
                : "border-slate-300 bg-white text-slate-700 hover:border-emerald-400")
            }
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
