const OPTIONS: ReadonlyArray<{ value: boolean; label: string }> = [
  { value: true, label: "עם ארוחה" },
  { value: false, label: "בלי ארוחה" },
];

interface MealChipsProps {
  selected: boolean;
  onSelect: (withMeal: boolean) => void;
  disabled?: boolean;
}

export default function MealChips({ selected, onSelect, disabled = false }: MealChipsProps) {
  return (
    <div role="radiogroup" aria-label="העדפת ארוחה" className="flex flex-wrap gap-2">
      {OPTIONS.map((option) => {
        const isSelected = option.value === selected;
        return (
          <button
            key={String(option.value)}
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
