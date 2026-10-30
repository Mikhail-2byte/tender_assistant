"""Offline packaging check: start the bundled Playwright driver, without a browser."""


def verify_platform_runtime():
    try:
        # Deliberate import probes: verify PyInstaller included these modules.
        from .platform_public import UGMKClient, TektorgClient  # noqa: F401
        import requests  # noqa: F401
        import bs4  # noqa: F401
        import py7zr  # noqa: F401
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runtime:
            assert runtime.chromium is not None
        return True
    except Exception:
        return False
