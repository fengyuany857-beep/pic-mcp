from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "trial04"
EVIDENCE = ARTIFACTS / "reference-host-evidence.json"
SCREENSHOT = ARTIFACTS / "reference-host-gallery.png"
HOST_URL = (
    "http://127.0.0.1:8080/"
    "?server=PicMCP%20Gallery%20Trial04"
    "&tool=recommend_gallery&call=true&theme=hide"
)


def persist(evidence: dict[str, object]) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


async def find_gallery_frame(page):
    for _ in range(120):
        for frame in page.frames:
            try:
                if await frame.locator("[data-gallery-root]").count():
                    return frame
            except Exception:
                pass
        await page.wait_for_timeout(250)
    raise AssertionError("MCP Apps gallery frame did not appear in official basic-host")


async def observe_loaded_images(frame, expected: int) -> tuple[list[dict[str, object]], bool]:
    await frame.locator("[data-gallery-image]").first.wait_for(state="attached", timeout=30000)
    latest: list[dict[str, object]] = []
    for _ in range(60):
        images = frame.locator("[data-gallery-image]")
        count = await images.count()
        if count == expected:
            latest = await images.evaluate_all(
                "els => els.map(img => ({src: img.currentSrc || img.src, complete: img.complete, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight}))"
            )
            if all(item["complete"] and item["naturalWidth"] > 0 for item in latest):
                return latest, True
            if all(item["complete"] for item in latest):
                return latest, False
        await frame.page.wait_for_timeout(250)
    return latest, bool(latest) and all(item["complete"] and item["naturalWidth"] > 0 for item in latest)


async def run() -> dict[str, object]:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, object] = {
        "trial": "04",
        "host": "modelcontextprotocol/ext-apps examples/basic-host v2.0.0",
        "host_url": HOST_URL,
        "status_dimensions": {},
        "checkpoint": "STARTED",
    }
    persist(evidence)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1200})
        console_errors: list[str] = []
        failed_requests: list[dict[str, object]] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(
                {
                    "url": req.url,
                    "failure": req.failure,
                    "resource_type": req.resource_type,
                }
            ),
        )
        await page.goto(HOST_URL, wait_until="domcontentloaded", timeout=60000)

        frame = await find_gallery_frame(page)
        await frame.locator("[data-card]").first.wait_for(state="visible", timeout=30000)
        card_count = await frame.locator("[data-card]").count()
        if card_count != 8:
            raise AssertionError(f"expected 8 rendered cards, got {card_count}")
        evidence["status_dimensions"]["GALLERY_RENDER_PASS"] = True
        evidence["checkpoint"] = "GALLERY_DOM_RENDERED"
        persist(evidence)

        image_observations, image_display_pass = await observe_loaded_images(frame, 8)
        evidence["initial_image_observations"] = image_observations
        evidence["status_dimensions"]["IMAGE_DISPLAY_PASS"] = image_display_pass
        if not image_display_pass:
            evidence["image_display_classification"] = "BROWSER_IMAGES_PRESENT_BUT_ONE_OR_MORE_DID_NOT_LOAD"
        evidence["checkpoint"] = "BROWSER_IMAGE_DISPLAY_OBSERVED"
        persist(evidence)

        first_before = await frame.locator("[data-card]").first.get_attribute("data-id")
        await frame.locator("[data-action='next']").click()
        await frame.locator("[data-card]").first.wait_for(state="visible", timeout=30000)
        for _ in range(80):
            first_after = await frame.locator("[data-card]").first.get_attribute("data-id")
            if first_after and first_after != first_before:
                break
            await page.wait_for_timeout(250)
        else:
            raise AssertionError("next action did not refresh gallery result")
        evidence["next_transition"] = {"before": first_before, "after": first_after}

        await frame.locator("[data-like]").first.click()
        await frame.locator("[data-status]").wait_for(state="visible", timeout=10000)
        for _ in range(40):
            status_text = await frame.locator("[data-status]").inner_text()
            if "Feedback acknowledged" in status_text:
                break
            await page.wait_for_timeout(250)
        else:
            raise AssertionError(f"feedback action did not return acknowledgement: {status_text}")

        first_before_similar = await frame.locator("[data-card]").first.get_attribute("data-id")
        await frame.locator("[data-similar]").first.click()
        for _ in range(80):
            first_after_similar = await frame.locator("[data-card]").first.get_attribute("data-id")
            if first_after_similar and first_after_similar != first_before_similar:
                break
            await page.wait_for_timeout(250)
        else:
            raise AssertionError("similar action did not refresh gallery result")

        evidence["similar_transition"] = {
            "before": first_before_similar,
            "after": first_after_similar,
        }
        evidence["status_dimensions"]["UI_ACTION_PASS"] = True
        evidence["status_dimensions"]["RECOMMENDATION_REFRESH_PASS"] = True
        evidence["checkpoint"] = "UI_INTERACTION_VERIFIED"

        await page.screenshot(path=str(SCREENSHOT), full_page=True)
        evidence["screenshot"] = str(SCREENSHOT.relative_to(ROOT))
        evidence["console_errors"] = console_errors
        evidence["failed_requests"] = failed_requests
        evidence["status_dimensions"]["REFERENCE_HOST_GALLERY_RENDER_PASS"] = True
        evidence["status_dimensions"]["CHATGPT_HOST_GALLERY_RENDER"] = "UNATTESTED"
        evidence["status_dimensions"]["MODEL_VISION_PASS"] = "NOT_IN_SCOPE"
        evidence["overall"] = "REFERENCE_HOST_PASS" if image_display_pass else "REFERENCE_HOST_PARTIAL_IMAGE_DISPLAY_FAILED"
        evidence["checkpoint"] = "COMPLETE"
        persist(evidence)
        await browser.close()

    return evidence


def main() -> int:
    try:
        evidence = asyncio.run(run())
    except Exception as exc:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        existing: dict[str, object] = {}
        if EVIDENCE.exists():
            try:
                existing = json.loads(EVIDENCE.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(
            {
                "trial": "04",
                "overall": "FAIL",
                "exception": f"{type(exc).__name__}: {exc}",
                "traceback": "".join(traceback.format_exception(exc)),
            }
        )
        persist(existing)
        print(json.dumps(existing, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"overall": evidence["overall"], "status_dimensions": evidence["status_dimensions"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
