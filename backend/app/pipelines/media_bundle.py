from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MediaBundle:
    title: str
    duration: float = 0.0
    video_paths: list[str] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    caption: str = ""

    @property
    def video_path(self) -> str | None:
        return self.video_paths[0] if self.video_paths else None

    @property
    def has_video(self) -> bool:
        return bool(self.video_paths)

    @property
    def has_images(self) -> bool:
        return bool(self.image_paths)
