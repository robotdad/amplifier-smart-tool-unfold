"""Library polling must not interrupt real decoded asset playback."""
import subprocess

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect, sync_playwright

from unfold import Unfold


def test_library_poll_preserves_preview_and_updates_changed_assets(tmp_path):
    video = tmp_path / "motion.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=30:duration=8", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(video),
    ], check=True, timeout=30)
    library = Unfold(tmp_path / "library")
    asset = library.import_asset(video, name="Motion preview", role="video")
    library.save_pack("Team", {}, [asset["id"]])
    with library.dashboard() as dashboard, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(dashboard.url)
        page.get_by_role("button", name="Library", exact=True).click()
        preview = page.locator("#packDetail video")
        preview.evaluate("v => { window.originalPreview = v; v.muted = true; v.play(); }")
        # Observe an actual background poll after playback has started.
        with page.expect_response(lambda r: r.url.endswith("/state")):
            page.wait_for_timeout(200)
        page.wait_for_timeout(500)
        assert preview.evaluate("v => v === window.originalPreview && !v.paused && v.readyState >= 2")
        page.locator("#packDetail").get_by_role("button", name="Preview Motion preview · Selected pack", exact=True).click()
        page.wait_for_timeout(500)
        dialog_video = page.locator("dialog video")
        assert dialog_video.evaluate("v => v.muted && v.loop && v.videoWidth === 320")
        dialog_video.evaluate("v => window.modalVideo = v")
        page.get_by_role("button", name="Close", exact=True).click()
        expect(page.locator("dialog video")).to_have_js_property("paused", True)
        library.rename(asset["id"], "Updated preview")
        expect(page.locator("#packDetail").get_by_text("Updated preview", exact=True)).to_be_visible(timeout=10000)
        browser.close()
