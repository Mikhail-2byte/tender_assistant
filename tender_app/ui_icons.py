"""Small bundled vector icon set; no network, fonts or external assets required."""

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "copy": '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    "chat": '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H7l-4 2V11.5a9 9 0 0 1 18 0zM7 10h10M7 14h6"/>',
    "trash": '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    "chevron_left": '<path d="m15 5-7 7 7 7"/>',
    "chevron_right": '<path d="m9 5 7 7-7 7"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2"/>',
    "panel_open": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18m4-13 4 4-4 4"/>',
    "panel_close": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18m8-13-4 4 4 4"/>',
    "filter": '<path d="M4 5h16l-6 7v7l-4-2v-5z"/>',
    "briefcase": '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4h8v3M3 12a23 23 0 0 0 18 0M10 12v3h4v-3"/>',
    "layers": '<path d="m3 7 9-5 9 5-9 5zM3 12l9 5 9-5M3 17l9 5 9-5"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    "sparkles": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
    "folder": '<path d="M3 7V5h7l2 3h9v12H3z"/>',
    "external": '<path d="M14 3h7v7m0-7L11 13M10 4H4v16h16v-6"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "save": '<path d="M4 3h13l4 4v14H3V3zM7 3v6h10V3M7 21v-8h10v8"/>',
    "edit": '<path d="m16 3 5 5-12 12-6 1 1-6zM13 6l5 5"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 17h.01"/>',
    "list": '<path d="M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
}


def icon(name, color="#b7c9e8"):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>'
    result = QIcon()
    for size in (20, 40):
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        QSvgRenderer(QByteArray(svg.encode())).render(painter)
        painter.end()
        result.addPixmap(image)
    return result
