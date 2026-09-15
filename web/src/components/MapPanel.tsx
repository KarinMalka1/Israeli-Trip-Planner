import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";
import type { Stop } from "../types";

interface MapPanelProps {
  stops: Stop[];
  highlightedPlaceId: string | null;
  onPinClick: (placeId: string) => void;
}

// Center of Israel — the only view a stop-free itinerary (loading, or a
// fallback-cards state with no day) or a day with zero geocoded stops ever
// shows. Never (0, 0): SPEC is explicit that a null-coordinate place is
// skipped, not plotted at the equator/prime-meridian null island.
const FALLBACK_CENTER: L.LatLngTuple = [31.5, 34.75];
const FALLBACK_ZOOM = 7;
const HIGHLIGHT_COLOR = "#dc2626"; // red-600 — distinct from the app's emerald so "selected" never reads as just "another stop"
const PIN_COLOR = "#059669"; // emerald-600, the app's one green

// A DivIcon's element is a plain <div>, not an <img> — Leaflet's own `alt`
// marker option only ever gets applied to an <img> (L.Icon), so passing it
// to a DivIcon marker is silent dead code (confirmed: getAttribute('alt')
// came back null in testing). `title` is set here instead, directly in the
// markup, which Leaflet does NOT strip from a DivIcon and which the
// browser turns into a native hover tooltip for free. It changes nothing
// for a screen reader — the whole map is aria-hidden, by design — this is
// purely a sighted-mouse-user nicety.
function buildIcon(number: number, label: string, highlighted: boolean): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<div title="${label.replace(/"/g, "&quot;")}" style="
        display:flex;align-items:center;justify-content:center;
        width:28px;height:28px;border-radius:9999px;
        background:${highlighted ? HIGHLIGHT_COLOR : PIN_COLOR};
        border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,0.4);
        color:white;font-size:12px;font-weight:700;font-family:inherit;
        transform:${highlighted ? "scale(1.2)" : "scale(1)"};
        transition:transform 150ms;
      ">${number}</div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

// Supplementary and read-only by design (SPEC section 5: no live routing,
// no road geometry — this shows sequence, not a route). Every interaction
// handler Leaflet ships with by default (drag, scroll-zoom, double-click
// zoom, box-zoom, touch-zoom, keyboard pan/zoom) is switched off below: the
// only thing that is ever allowed to move the view is fitBounds, called
// once per stops change. That is also what keeps this out of the Tab
// order — Leaflet's keyboard handler is what adds tabindex to the map
// container, and it is the one being disabled.
export default function MapPanel({ stops, highlightedPlaceId, onPinClick }: MapPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<globalThis.Map<string, L.Marker>>(new globalThis.Map());
  const polylineRef = useRef<L.Polyline | null>(null);
  const onPinClickRef = useRef(onPinClick);
  onPinClickRef.current = onPinClick;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, {
      center: FALLBACK_CENTER,
      zoom: FALLBACK_ZOOM,
      zoomControl: false,
      attributionControl: false,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      touchZoom: false,
      keyboard: false,
      fadeAnimation: false,
      zoomAnimation: false,
      markerZoomAnimation: false,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
    }).addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Rebuilds markers + the dashed sequence line and re-fits bounds — but
  // only when the stop list itself changes (create/remove/swap/undo all
  // hand this component a new `stops` array). Highlight changes are
  // handled by the effect below instead, deliberately not listed here:
  // hovering a card must recolour a pin without ever re-panning the map
  // ("no motion beyond the initial fit" covers highlight, not just load).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current.clear();
    polylineRef.current?.remove();
    polylineRef.current = null;

    const points: L.LatLngTuple[] = [];
    stops.forEach((stop, index) => {
      const { lat, lng } = stop.place;
      if (lat == null || lng == null) return; // skipped silently, never plotted at 0,0
      points.push([lat, lng]);
      const label = `עצירה ${index + 1}: ${stop.place.name_he}`;
      const marker = L.marker([lat, lng], {
        icon: buildIcon(index + 1, label, stop.place_id === highlightedPlaceId),
        keyboard: false,
      });
      marker.on("click", () => onPinClickRef.current(stop.place_id));
      marker.addTo(map);
      markersRef.current.set(stop.place_id, marker);
    });

    if (points.length > 1) {
      polylineRef.current = L.polyline(points, {
        color: PIN_COLOR,
        weight: 2,
        dashArray: "6 6",
      }).addTo(map);
    }

    if (points.length === 1) {
      map.setView(points[0], 13, { animate: false });
    } else if (points.length > 1) {
      map.fitBounds(L.latLngBounds(points), { padding: [28, 28], animate: false });
    }
    // highlightedPlaceId deliberately excluded — see comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stops]);

  // Recolours the affected pins only, no rebuild and no re-fit.
  useEffect(() => {
    stops.forEach((stop, index) => {
      const marker = markersRef.current.get(stop.place_id);
      const label = `עצירה ${index + 1}: ${stop.place.name_he}`;
      marker?.setIcon(buildIcon(index + 1, label, stop.place_id === highlightedPlaceId));
    });
  }, [highlightedPlaceId, stops]);

  return (
    <div className="flex h-full w-full flex-col gap-1">
      {/* The map is supplementary and carries no information the list
          doesn't already have (the numbers mirror "עצירה N", the dashed
          line mirrors the list's own order) — aria-hidden here, on the
          Leaflet container specifically, is what keeps a screen reader
          user on the real list instead of an unlabelled soup of tile
          images and DOM markers. The OSM attribution below is
          deliberately OUTSIDE this aria-hidden div and rendered as our
          own link rather than Leaflet's built-in control (which lives
          inside the map container) — a hidden-but-focusable link is a
          real accessibility bug, not a theoretical one. */}
      <div ref={containerRef} aria-hidden="true" className="min-h-0 w-full flex-1 rounded-xl" />
      <p className="shrink-0 text-center text-[10px] text-slate-400">
        <a
          href="https://www.openstreetmap.org/copyright"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-slate-600 hover:underline"
        >
          © OpenStreetMap contributors
        </a>
      </p>
    </div>
  );
}
