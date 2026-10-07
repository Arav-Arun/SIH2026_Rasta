/// <reference types="vite/client" />
import * as maplibregl from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';

// MapLibre 6 finds its tile worker next to its own module, which a bundle does
// not keep. Point it at the worker file the build emits, on this origin, so the
// CSP needs no blob: workers for it. Every map imports MapLibre from here.
maplibregl.setWorkerUrl(workerUrl);

export default maplibregl;
