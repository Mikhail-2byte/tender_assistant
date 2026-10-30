"""Render the editable SVG into a multi-resolution Windows ICO using Qt only."""
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def build():
    assets = Path(__file__).resolve().parents[1] / "tender_app" / "assets"
    renderer = QSvgRenderer(str(assets / "tender-assistant.svg"))
    if not renderer.isValid():
        raise ValueError("Invalid application icon SVG")
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    frames = []
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Cannot render application icon")
        frames.append(bytes(buffer.data()))
    offset = 6 + 16 * len(sizes)
    entries = []
    for size, frame in zip(sizes, frames):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    (assets / "tender-assistant.ico").write_bytes(
        struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(entries) + b"".join(frames)
    )


if __name__ == "__main__":
    build()
