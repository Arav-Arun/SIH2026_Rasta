/** Web Mercator maths for drawing a route over standard 256-point map tiles. */

export type LngLat = [number, number];
export type Point = { x: number; y: number };
export type Tile = {
  key: string;
  zoom: number;
  x: number;
  y: number;
  left: number;
  top: number;
};
export type Drawing = { tiles: Tile[]; lines: Point[][]; tileSize: number };

const MAX_ZOOM = 17;

/** Position of a coordinate in points at the given zoom, from the world's top left. */
export function project([lng, lat]: LngLat, zoom: number): Point {
  const world = 256 * 2 ** zoom;
  const sin = Math.sin((Math.max(-85, Math.min(85, lat)) * Math.PI) / 180);
  return {
    x: ((lng + 180) / 360) * world,
    y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * world,
  };
}

function extent(points: Point[]) {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  return {
    width: Math.max(...xs) - Math.min(...xs),
    height: Math.max(...ys) - Math.min(...ys),
    centreX: (Math.min(...xs) + Math.max(...xs)) / 2,
    centreY: (Math.min(...ys) + Math.max(...ys)) / 2,
  };
}

/**
 * The deepest zoom at which every line fits inside the frame with `padding` to
 * spare, the tiles that cover the frame, and the lines in frame points. With
 * `sharp`, tiles come from one zoom deeper at half size, for dense screens.
 */
export function fitLines(
  lines: LngLat[][],
  frame: { width: number; height: number; padding: number },
  sharp = false,
): Drawing {
  const points = lines.flat();
  let zoom = MAX_ZOOM;
  while (zoom > 2) {
    const box = extent(points.map((point) => project(point, zoom)));
    if (
      box.width <= frame.width - 2 * frame.padding &&
      box.height <= frame.height - 2 * frame.padding
    )
      break;
    zoom -= 1;
  }
  const box = extent(points.map((point) => project(point, zoom)));
  const left = box.centreX - frame.width / 2;
  const top = box.centreY - frame.height / 2;

  const tileZoom = sharp ? zoom + 1 : zoom;
  const tileSize = sharp ? 128 : 256;
  const count = 2 ** tileZoom;
  const tiles: Tile[] = [];
  for (
    let ty = Math.floor(top / tileSize);
    ty * tileSize < top + frame.height;
    ty += 1
  ) {
    if (ty < 0 || ty >= count) continue;
    for (
      let tx = Math.floor(left / tileSize);
      tx * tileSize < left + frame.width;
      tx += 1
    ) {
      tiles.push({
        key: `${tileZoom}/${tx}/${ty}`,
        zoom: tileZoom,
        x: ((tx % count) + count) % count,
        y: ty,
        left: tx * tileSize - left,
        top: ty * tileSize - top,
      });
    }
  }
  const framed = lines.map((line) =>
    line.map((point) => {
      const p = project(point, zoom);
      return { x: p.x - left, y: p.y - top };
    }),
  );
  return { tiles, lines: framed, tileSize };
}
