from tender_app.settings import Settings


def test_damaged_settings_do_not_prevent_start(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{invalid", encoding="utf-8")
    settings = Settings(path)
    assert settings.load_warning
    assert settings.get("group", "В работе") == "В работе"
    settings.save(group="Все", selected_id=123)
    reopened = Settings(path)
    assert reopened.get("group", "В работе") == "Все"
    assert reopened.get("selected_id") == 123


def test_wrong_setting_type_uses_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"window_size": "wrong"}', encoding="utf-8")
    assert Settings(path).get("window_size", [1240, 900]) == [1240, 900]
