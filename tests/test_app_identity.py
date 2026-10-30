from tender_app.app_identity import application_icon


def test_application_icon_loads_outside_project(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    icon = application_icon()
    assert not icon.isNull()
    assert {size.width() for size in icon.availableSizes()} == {
        16, 20, 24, 32, 40, 48, 64, 128, 256,
    }
    for size in (16, 32, 48, 256):
        image = icon.pixmap(size, size).toImage()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(size // 2, size // 2).alpha() == 255
