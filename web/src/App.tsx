import { useEffect, useState } from "react";
import { getHealth, getPlaces } from "./api/client";
import type { HealthStatus, Place } from "./types";

// Deliberately bare: this proves the frontend can reach the FastAPI backend
// before any real UI (region chips, itinerary rendering) is built on top.
function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [places, setPlaces] = useState<Place[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getHealth(), getPlaces()])
      .then(([healthResult, placesResult]) => {
        setHealth(healthResult);
        setPlaces(placesResult);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      });
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 32 }}>
      <h1>Israeli Day Trip Planner — connectivity check</h1>

      {error && (
        <p style={{ color: "crimson" }}>
          Could not reach the API: {error}
          <br />
          Is <code>uvicorn api.main:app --reload</code> running on port 8000?
        </p>
      )}

      {!error && !health && <p>Connecting to API…</p>}

      {health && (
        <ul>
          <li>API status: {health.status}</li>
          <li>Places loaded: {health.places}</li>
          <li>Distance matrix loaded: {String(health.matrix_loaded)}</li>
          <li>Places returned by GET /api/places: {places?.length ?? "…"}</li>
        </ul>
      )}
    </main>
  );
}

export default App;
