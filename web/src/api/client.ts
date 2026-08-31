// Thin wrapper around the /api endpoints (SPEC.md section 7). No time
// arithmetic or validation lives here or anywhere on the client: every call
// just forwards the request and hands back what the server computed.

import type { CreateItineraryRequest, Itinerary, PlaceRefRequest } from "../types";

// Points at the local FastAPI dev server (`uvicorn api.main:app --reload`).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Carries the HTTP status so callers can special-case a 404 (US-3: a
// resumed itinerary that no longer exists on the server) without parsing
// the message string.
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    throw new ApiError(await readErrorDetail(response), response.status);
  }
  return response.json() as Promise<T>;
}

// Every error response carries a Hebrew `detail` (main.py's module docstring,
// rule 13) — that string is exactly what the UI should show, not a generic
// "request failed".
async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // body wasn't JSON — fall through to the generic message below.
  }
  return `${response.status} ${response.statusText}`;
}

export function createItinerary(body: CreateItineraryRequest): Promise<Itinerary> {
  return request<Itinerary>("/api/itinerary", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getItinerary(id: string): Promise<Itinerary> {
  return request<Itinerary>(`/api/itinerary/${id}`);
}

export function removeStop(id: string, placeId: string): Promise<Itinerary> {
  const body: PlaceRefRequest = { place_id: placeId };
  return request<Itinerary>(`/api/itinerary/${id}/remove`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function swapStop(id: string, placeId: string): Promise<Itinerary> {
  const body: PlaceRefRequest = { place_id: placeId };
  return request<Itinerary>(`/api/itinerary/${id}/swap`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function undoEdit(id: string): Promise<Itinerary> {
  return request<Itinerary>(`/api/itinerary/${id}/undo`, { method: "POST" });
}
