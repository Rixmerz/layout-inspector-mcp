"""Rectangle maths shared by the issue detectors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """An axis-aligned box in CSS pixels, viewport-relative."""

    x: float
    y: float
    w: float
    h: float

    @classmethod
    def from_dict(cls, data: dict) -> Rect:
        return cls(
            float(data.get("x", 0.0)),
            float(data.get("y", 0.0)),
            float(data.get("w", 0.0)),
            float(data.get("h", 0.0)),
        )

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    @property
    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    def intersects(self, other: Rect) -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )

    def intersection(self, other: Rect) -> Rect:
        x = max(self.x, other.x)
        y = max(self.y, other.y)
        return Rect(
            x,
            y,
            max(0.0, min(self.right, other.right) - x),
            max(0.0, min(self.bottom, other.bottom) - y),
        )

    def intersection_area(self, other: Rect) -> float:
        if not self.intersects(other):
            return 0.0
        return self.intersection(other).area
