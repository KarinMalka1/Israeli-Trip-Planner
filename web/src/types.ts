// Hand-mirrored from SPEC.md section 7 (the shared API contract), including
// the section 10/11 amendments. Keep this in sync with api/models.py by hand
// until codegen is worth it.

export type PlaceId = string;
export type Region = "north" | "central" | "south";
export type MaxLegMin = 20 | 45 | 90;
export type StartsAt = "08:00" | "09:00" | "10:00" | "11:00";
export type DayLength = "short" | "long";
export type Weekday = "sun" | "mon" | "tue" | "wed" | "thu" | "fri" | "sat";
export type Category =
  | "museum"
  | "nature"
  | "hike"
  | "viewpoint"
  | "meal"
  | "kids"
  | "historic";
export type AccessType = "gated" | "open";
export type DescriptionSource = "generated" | "human";
export type Season = "year_round" | "summer_only";

// A local path under /images/ (never a hotlinked commons URL), plus the
// attribution CC BY/BY-SA legally requires. All three fields are required —
// the backend refuses to construct a Place with an under-attributed image.
export interface PlaceImage {
  url: string;
  credit: string;
  source_url: string;
}

export interface Place {
  id: PlaceId;
  name_he: string;
  description_he: string;
  tip_he: string;
  description_source: DescriptionSource;
  category: Category;
  region: Region;
  access: AccessType;
  // null = not yet resolved (SPEC section 10 amendment); such a place is
  // never scheduled, so the UI never needs to render a missing coordinate.
  lat: number | null;
  lng: number | null;
  duration_min: number;
  opening_hours: Record<Weekday, [string, string] | null>;
  hours_verified: boolean;
  closed_on_shabbat: boolean;
  kid_friendly: boolean;
  accessible: boolean;
  tags: string[];
  season: Season;
  // Up to 3, Wikimedia Commons only. Most places have none — render the
  // deliberate fallback, not a broken image icon.
  images: PlaceImage[];
  // Link to the place's own official page. null renders no anchor at all.
  official_url: string | null;
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
  // Whether the day actually includes a meal stop. with_meal on the request
  // is a preference, not a guarantee (only 3 meal places exist nationwide) —
  // this is what the server actually managed, so the client can say so when
  // it asked for one and didn't get it.
  meal_included: boolean;
  // Whether the day's elapsed time landed inside the requested day_length's
  // target band. Same shape as meal_included: day_length is a preference,
  // not a guarantee, and this is what the server actually managed.
  length_matched: boolean;
}

export interface CreateItineraryRequest {
  session_id: string;
  region: Region;
  max_leg_min: MaxLegMin;
  weekday: Weekday;
  prompt_he?: string;
  chip?: string;
  // A preference, not a hard constraint — see Itinerary.meal_included.
  with_meal?: boolean;
  starts_at?: StartsAt;
  // A preference, not a hard constraint — see Itinerary.length_matched.
  day_length?: DayLength;
}

export interface PlaceRefRequest {
  place_id: PlaceId;
}
