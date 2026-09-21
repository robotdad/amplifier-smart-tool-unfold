"""Independent official-AppBridge proof that MCP runs the native dashboard."""

import asyncio
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("playwright")
from mcp import Client
from mcp_fixtures import seed_media_library
from playwright.async_api import async_playwright, expect

from unfold.mcp import create_server

ROOT = Path(__file__).parents[1]


def mcp_html():
    return files("unfold").joinpath("resources/mcp_app.html").read_text()


def host_script():
    return subprocess.run(
        [
            str(ROOT / "mcp-app" / "node_modules" / ".bin" / "esbuild"),
            str(ROOT / "mcp-app" / "test-host.js"),
            "--bundle",
            "--format=iife",
            "--log-level=error",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.mark.parametrize("duration", [5, 2.5, 1/30])
def test_mcp_app_is_native_dashboard_with_retained_media_and_feedback(tmp_path, duration):
    async def run():
        library, _, revisions, _ = seed_media_library(tmp_path, duration=duration)
        async with Client(create_server(library)) as client, async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1180, "height": 900})
            calls, errors = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def call(params):
                calls.append(params)
                result = await client.call_tool(params["name"], params.get("arguments", {}))
                return result.model_dump(by_alias=True, exclude_none=True)

            async def read(params):
                result = await client.read_resource(params["uri"])
                return result.model_dump(by_alias=True, exclude_none=True)

            await page.expose_function("hostCall", call)
            await page.expose_function("hostRead", read)
            await page.goto("about:blank")
            await page.add_script_tag(content=host_script())
            initial = await client.call_tool("unfold_review_state", {})
            html = mcp_html()
            await page.evaluate(
                "([html,result])=>mountUnfold(html,result)",
                [html, initial.model_dump(by_alias=True, exclude_none=True)],
            )
            frame = page.frame_locator("#app")
            await expect(frame.locator("aside")).to_be_visible()
            await expect(frame.locator("nav")).to_have_text("ReviewLibraryExport")
            await expect(frame.locator("#title")).not_to_have_text("Unfold")
            video = frame.locator("#players video")
            await expect(video).to_be_visible()
            await expect(video).to_have_js_property("videoWidth", 320)
            assert abs(await video.evaluate("v => v.duration") - duration) < 0.001
            scrub = frame.locator("#scrub")
            assert float(await scrub.get_attribute("max")) == duration
            assert await scrub.get_attribute("step") == "any"
            at = max(0, duration - 1/30)
            await scrub.evaluate("(el, at) => {el.value = String(at); el.dispatchEvent(new Event('input', {bubbles:true}));}", at)
            await expect(video).to_have_js_property("paused", True)
            assert abs(await video.evaluate("v => v.currentTime") - at) < 0.001
            assert (await video.get_attribute("src")).startswith("blob:")
            await frame.locator("#compare").click()
            await expect(frame.locator("#players video")).to_have_count(2)
            await frame.locator("#single").click()
            await frame.locator("#feedbackToggle").click()
            draft = "Keep the handoff visible"
            await frame.locator("#feedback").fill(draft)
            await expect(frame.locator("#draftStatus")).to_contain_text("Draft retained")
            await frame.locator("#note").click()
            await expect(frame.locator("#notice")).to_contain_text("Comment retained")
            assert library.store.list("feedback")[0]["revision_id"] == revisions[-1]
            assert library.store.list("feedback")[0]["text"] == draft
            assert any(call["name"] == "unfold_save_draft" for call in calls)
            assert any(call["name"] == "unfold_feedback" for call in calls)
            assert not any(
                call["name"] in {"unfold_submit_creation", "unfold_submit_refinement"}
                for call in calls
            )
            await page.set_viewport_size({"width": 440, "height": 900})
            assert await frame.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
            await page.screenshot(path=str(tmp_path / "mcp-native-dashboard.png"), full_page=True)
            assert not errors, errors
            await browser.close()

    asyncio.run(run())


def test_mcp_app_reports_missing_resource_capability_without_localhost_fallback(tmp_path):
    async def run():
        library, _, _, _ = seed_media_library(tmp_path)
        async with Client(create_server(library)) as client, async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 440, "height": 900})

            async def call(params):
                result = await client.call_tool(params["name"], params.get("arguments", {}))
                return result.model_dump(by_alias=True, exclude_none=True)

            async def read(params):
                result = await client.read_resource(params["uri"])
                return result.model_dump(by_alias=True, exclude_none=True)

            await page.expose_function("hostCall", call)
            await page.expose_function("hostRead", read)
            await page.goto("about:blank")
            await page.add_script_tag(content=host_script())
            initial = await client.call_tool("unfold_review_state", {})
            html = mcp_html()
            await page.evaluate(
                "([html,result])=>mountUnfold(html,result,false)",
                [html, initial.model_dump(by_alias=True, exclude_none=True)],
            )
            frame = page.frame_locator("#app")
            await expect(frame.locator("#notice")).to_contain_text(
                "does not support MCP resource reads"
            )
            assert await frame.locator("#players video").get_attribute("src") is None
            assert "http://127.0.0.1" not in html
            await browser.close()

    asyncio.run(run())


def test_mcp_poll_preserves_active_playback_and_invalidates_tampered_output(tmp_path):
    async def run():
        library, _, _, artifacts = seed_media_library(tmp_path)
        artifact_path = library.store.root / library.store.get(artifacts[-1])["relative_path"]
        original = artifact_path.read_bytes()
        async with Client(create_server(library)) as client, async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1180, "height": 900})

            async def call(params):
                result = await client.call_tool(params["name"], params.get("arguments", {}))
                return result.model_dump(by_alias=True, exclude_none=True)

            async def read(params):
                result = await client.read_resource(params["uri"])
                return result.model_dump(by_alias=True, exclude_none=True)

            await page.expose_function("hostCall", call)
            await page.expose_function("hostRead", read)
            await page.goto("about:blank")
            await page.add_script_tag(content=host_script())
            initial = await client.call_tool("unfold_review_state", {})
            await page.evaluate(
                "([html,result])=>mountUnfold(html,result)",
                [mcp_html(), initial.model_dump(by_alias=True, exclude_none=True)],
            )
            frame = page.frame_locator("#app")
            player = frame.locator("#players video")
            await expect(player).to_have_js_property("videoWidth", 320)
            handle = await player.element_handle()
            await frame.locator("#play").click()
            await page.wait_for_timeout(3400)
            assert await handle.evaluate("video => video.isConnected")
            assert not await player.evaluate("video => video.paused")
            assert await player.evaluate("video => video.currentTime") > 2

            artifact_path.write_bytes(b"changed fixture bytes")
            try:
                await page.wait_for_timeout(3400)
                await frame.locator('nav [data-page="delivery"]').click()
                await expect(frame.locator("#outputs video")).to_have_count(0)
                await expect(frame.locator("#outputs")).to_contain_text("Output missing or changed")
                assert "could not be played" not in await frame.locator("#notice").inner_text()
            finally:
                artifact_path.write_bytes(original)
            await browser.close()

    asyncio.run(run())


def test_mcp_teardown_flushes_debounced_draft_before_bridge_closes(tmp_path):
    async def run():
        library, _, revisions, _ = seed_media_library(tmp_path)
        async with Client(create_server(library)) as client, async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1180, "height": 900})
            calls = []

            async def call(params):
                calls.append(params["name"])
                result = await client.call_tool(params["name"], params.get("arguments", {}))
                return result.model_dump(by_alias=True, exclude_none=True)

            async def read(params):
                return (await client.read_resource(params["uri"])).model_dump(
                    by_alias=True, exclude_none=True
                )

            await page.expose_function("hostCall", call)
            await page.expose_function("hostRead", read)
            await page.goto("about:blank")
            await page.add_script_tag(content=host_script())
            initial = await client.call_tool("unfold_review_state", {})
            await page.evaluate(
                "([html,result])=>mountUnfold(html,result)",
                [mcp_html(), initial.model_dump(by_alias=True, exclude_none=True)],
            )
            frame = page.frame_locator("#app")
            await expect(frame.locator("#players video")).to_have_js_property("videoWidth", 320)
            await frame.locator("#feedbackToggle").click()
            # Input and teardown in one event turn: the debounce cannot fire first.
            await page.evaluate("""() => {
                window.teardownDone = new Promise((resolve, reject) => {
                    const listener = event => {
                        if (!event.data?.fixtureTeardown) return;
                        window.removeEventListener('message', listener);
                        unfoldBridge.teardownResource({}).then(resolve, reject);
                    };
                    window.addEventListener('message', listener);
                });
            }""")
            await frame.locator("#feedback").evaluate(
                """element => {
                    element.value = 'Last edit before closing';
                    element.dispatchEvent(new Event('input', {bubbles: true}));
                    parent.postMessage({fixtureTeardown: true}, '*');
                }"""
            )
            await page.evaluate("() => window.teardownDone")
            draft = next(
                d for d in library.review_state()["drafts"] if d["revision_id"] == revisions[-1]
            )
            assert draft["text"] == "Last edit before closing"
            assert "unfold_submit_refinement" not in calls
            assert "unfold_feedback" not in calls
            await browser.close()

    asyncio.run(run())
