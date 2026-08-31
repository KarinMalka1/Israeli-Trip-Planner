// Thin wrapper around the /api endpoints (SPEC.md section 7). No time
// arithmetic or validation lives here or anywhere on the client: every call
// just forwards the request and hands back what the server computed.

import type { HealthStatus, Place, Region } from "../types";

// Points at the local FastAPI dev server (`uvicorn api.main:app --reload`).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`${path} -> ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function getHealth(): Promise<HealthStatus> {
  return request<HealthStatus>("/api/health");
}

export function getPlaces(region?: Region): Promise<Place[]> {
  const query = region ? `?region=${region}` : "";
  return request<Place[]>(`/api/places${query}`);
}
