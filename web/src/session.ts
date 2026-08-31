// US-3: "session UUID in localStorage resolves to a saved itinerary on the
// server, restored on load." Two separate keys on purpose, matching
// api/main.py's create_itinerary comment: `session_id` identifies the
// browser and is sent on every create call, while the itinerary's own `id`
// is minted server-side per plan and is what GET/remove/swap/undo key on —
// one browser may have planned several itineraries, only the latest is resumed.

const SESSION_ID_KEY = "trip-planner-session-id";
const ITINERARY_ID_KEY = "trip-planner-itinerary-id";

export function getOrCreateSessionId(): string {
  const existing = localStorage.getItem(SESSION_ID_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID();
  localStorage.setItem(SESSION_ID_KEY, created);
  return created;
}

export function getStoredItineraryId(): string | null {
  return localStorage.getItem(ITINERARY_ID_KEY);
}

export function setStoredItineraryId(id: string): void {
  localStorage.setItem(ITINERARY_ID_KEY, id);
}

export function clearStoredItineraryId(): void {
  localStorage.removeItem(ITINERARY_ID_KEY);
}
