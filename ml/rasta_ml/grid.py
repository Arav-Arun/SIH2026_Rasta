"""The cell grid: the rainfall record's own 0.25 degree cells, clipped to the region."""

from __future__ import annotations

import math
from dataclasses import dataclass

from rasta_ml.manifest import Region


@dataclass(frozen=True, slots=True)
class Grid:
    """Cells numbered row by row from the north-west corner.

    Cell edges fall on multiples of ``step``, as in the rainfall record, so a cell
    here is exactly one rainfall value and never an interpolation of several.
    """

    west: float
    south: float
    east: float
    north: float
    step: float

    @classmethod
    def for_region(cls, region: Region, step: float) -> Grid:
        return cls(region.west, region.south, region.east, region.north, step)

    @property
    def nx(self) -> int:
        return round((self.east - self.west) / self.step)

    @property
    def ny(self) -> int:
        return round((self.north - self.south) / self.step)

    def __len__(self) -> int:
        return self.nx * self.ny

    def cell_of(self, lat: float, lon: float) -> int | None:
        """The cell holding a point, or None outside the grid."""

        if not (math.isfinite(lat) and math.isfinite(lon)):
            return None
        col = math.floor((lon - self.west) / self.step)
        row = math.floor((self.north - lat) / self.step)
        if 0 <= col < self.nx and 0 <= row < self.ny:
            return row * self.nx + col
        return None

    def row_col(self, cell: int) -> tuple[int, int]:
        return divmod(cell, self.nx)

    def bounds(self, cell: int) -> tuple[float, float, float, float]:
        """(west, south, east, north) of a cell."""

        row, col = self.row_col(cell)
        west = self.west + col * self.step
        north = self.north - row * self.step
        return west, north - self.step, west + self.step, north

    def centre(self, cell: int) -> tuple[float, float]:
        """(lat, lon) of a cell's centre."""

        west, south, east, north = self.bounds(cell)
        return (south + north) / 2, (west + east) / 2

    def block_of(self, cell: int, block_deg: float) -> tuple[int, int]:
        """The whole-degree-aligned block a cell falls in, for the spatial check."""

        lat, lon = self.centre(cell)
        return math.floor(lat / block_deg), math.floor(lon / block_deg)


__all__ = ["Grid"]
