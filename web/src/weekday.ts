import type { Weekday } from "./types";

// Sunday-first, matching api/models.py's Weekday enum and the seed file's
// own JSON keys. Date.getDay() is already Sunday-indexed (0=Sun..6=Sat), so
// this is a direct lookup, not a computation about any itinerary's schedule
// — reading today's date, unlike arrive_at/duration/travel, is not the kind
// of time arithmetic the API contract reserves for the server.
const SUNDAY_FIRST: readonly Weekday[] = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

export function getTodayWeekday(): Weekday {
  return SUNDAY_FIRST[new Date().getDay()];
}
