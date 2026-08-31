// Hand-mirrored from SPEC.md section 7 (the shared API contract).
// Keep this in sync with api/models.py by hand until codegen is worth it.

export type PlaceId = string;
export type Region = "north" | "central" | "south";
export type MaxLegMin = 20 | 45 | 90;
export type Weekday = "sun" | "mon" | "tue" | "wed" | "thu" | "fri" | "sat";
export type Category =
  | "museum"
  | "nature"
  | "hike"
  | "viewpoint"
  | "meal"
  | "kids"
  | "historic";

export interface Place {
  id: PlaceId;
  name_he: string;
  description_he: string;
  tip_he: string;
  category: Category;
  region: Region;
  lat: number;
  lng: number;
  duration_min: number;
  opening_hours: Record<Weekday, [string, string] | null>;
  closed_on_shabbat: boolean;
  kid_friendly: boolean;
  accessible: boolean;
  tags: string[];
}

export interface Stop {
  place_id: PlaceId;
  place: Place;
  arrive_at: string;
  duration_min: number;
  travel_min_from_prev: number;
}

export interface Day {
  stops: Stop[];
  starts_at: string;
  ends_at: string;
}

export interface Itinerary {
  id: string;
  region: Region;
  max_leg_min: MaxLegMin;
  weekday: Weekday;
  days: Day[];
  places: Place[];
  relaxed_to: MaxLegMin | null;
}

export interface HealthStatus {
  status: string;
  places: number;
  matrix_loaded: boolean;
}
