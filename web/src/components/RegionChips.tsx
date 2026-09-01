import type { Region } from "../types";

const REGIONS: ReadonlyArray<{ id: Region; label: string }> = [
  { id: "north", label: "צפון" },
  { id: "central", label: "מרכז" },
  { id: "south", label: "דרום" },
];

interface RegionChipsProps {
  selected: Region;
  onSelect: (region: Region) => void;
  disabled?: boolean;
}

export default function RegionChips({ selected, onSelect, disabled = false }: RegionChipsProps) {
  return (
    <div role="radiogroup" aria-label="אזור" className="flex flex-wrap gap-2">
      {REGIONS.map((region) => {
        const isSelected = region.id === selected;
        return (
          <button
            key={region.id}
            type="button"
            role="radio"
            aria-checked={isSelected}
            disabled={disabled}
            onClick={() => onSelect(region.id)}
            className={
              "rounded-full border px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 md:text-base " +
              (isSelected
                ? "border-emerald-600 bg-emerald-600 text-white"
                : "border-slate-300 bg-white text-slate-700 hover:border-emerald-400")
            }
          >
            {region.label}
          </button>
        );
      })}
    </div>
  );
}
