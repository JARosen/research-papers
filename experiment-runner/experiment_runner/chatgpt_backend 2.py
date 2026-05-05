from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import RunnerConfig

try:
    from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
except Exception:  # pragma: no cover - lazy import for environments without Playwright
    BrowserContext = Page = object  # type: ignore[assignment,misc]
    PlaywrightTimeoutError = Exception  # type: ignore[assignment]
    sync_playwright = None  # type: ignore[assignment]


ASSISTANT_SELECTORS = [
    "[data-message-author-role='assistant']",
    "article [data-message-author-role='assistant']",
    "main article",
]
COMPOSER_SELECTORS = [
    "#prompt-textarea",
    "textarea",
    "div[contenteditable='true']#prompt-textarea",
    "div[contenteditable='true']",
]
SEND_BUTTON_SELECTORS = [
    "button[data-testid='send-button']",
    "button[aria-label*='Send']",
    "button:has(svg)",
]
STOP_BUTTON_SELECTORS = [
    "button[data-testid='stop-button']",
    "button[aria-label*='Stop']",
]
NEW_CHAT_SELECTORS = [
    "a[href='/']",
    "button:has-text('New chat')",
    "[data-testid='new-chat-button']",
]
TEMPORARY_CHAT_SELECTORS = [
    "button:has-text('Temporary')",
    "[aria-label*='Temporary']",
]
TEMPORARY_ACTIVE_MARKERS = [
    "text=Temporary Chat",
    "text=Temporary",
]


@dataclass
class ChatRunResult:
    prompt: str
    final_text: str
    duration_s: float
    conversation_url: str


class ChatGPTBaselineRunner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed. Run `pip install -e .` first.")

    def bootstrap_login(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.config.chatgpt_cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(self.config.chatgpt_url)
            print(
                "Attached to existing Chrome over CDP. Verify you are logged in, "
                "then press Enter here to continue."
            )
            input()
            browser.close()

    def run_repeated_trials(self, prompts: list[str]) -> list[ChatRunResult]:
        results: list[ChatRunResult] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.config.chatgpt_cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(self.config.chatgpt_url)
            for prompt in prompts:
                self._start_new_chat(page)
                self._ensure_temporary_chat(page)
                results.append(self._submit_prompt(page, prompt))
            page.close()
            browser.close()
        return results

    def run_initial_and_update(self, initial_prompt: str, update_prompt: str) -> tuple[ChatRunResult, ChatRunResult]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.config.chatgpt_cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(self.config.chatgpt_url)
            self._start_new_chat(page)
            self._ensure_temporary_chat(page)
            initial = self._submit_prompt(page, initial_prompt)
            updated = self._submit_prompt(page, update_prompt)
            page.close()
            browser.close()
        return initial, updated

    def _start_new_chat(self, page: Page) -> None:
        for selector in NEW_CHAT_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(timeout=2_000)
                locator.click()
                page.wait_for_timeout(1_500)
                break
            except Exception:
                continue
        else:
            page.goto(self.config.chatgpt_url)
            page.wait_for_timeout(1_500)

    def _ensure_temporary_chat(self, page: Page) -> None:
        if self._is_temporary_chat_active(page):
            return
        for selector in TEMPORARY_CHAT_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(timeout=3_000)
                locator.click()
                page.wait_for_timeout(1_500)
                if self._is_temporary_chat_active(page):
                    return
            except Exception:
                continue
        raise RuntimeError(
            "Could not enable ChatGPT Temporary Chat automatically. "
            "Open a new chat and click the Temporary pill manually, then rerun."
        )

    def _is_temporary_chat_active(self, page: Page) -> bool:
        for selector in TEMPORARY_ACTIVE_MARKERS:
            try:
                if page.locator(selector).first.is_visible(timeout=750):  # type: ignore[call-arg]
                    return True
            except Exception:
                continue
        return False

    def _submit_prompt(self, page: Page, prompt: str) -> ChatRunResult:
        composer = self._find_composer(page)
        previous_count = self._assistant_count(page)
        start = time.perf_counter()
        try:
            composer.fill(prompt)  # type: ignore[union-attr]
        except Exception:
            composer.click()  # type: ignore[union-attr]
            composer.press_sequentially(prompt)  # type: ignore[union-attr]
        self._click_send(page)
        final_text = self._wait_for_last_assistant_message(page, previous_count=previous_count)
        duration_s = time.perf_counter() - start
        return ChatRunResult(
            prompt=prompt,
            final_text=final_text,
            duration_s=duration_s,
            conversation_url=page.url,
        )

    def _find_composer(self, page: Page):
        for selector in COMPOSER_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(timeout=4_000)
                return locator
            except Exception:
                continue
        raise RuntimeError("Could not find ChatGPT composer.")

    def _click_send(self, page: Page) -> None:
        for selector in SEND_BUTTON_SELECTORS:
            locator = page.locator(selector).first
            try:
                if locator.is_visible(timeout=2_000):  # type: ignore[call-arg]
                    locator.click()
                    return
            except Exception:
                continue
        composer = self._find_composer(page)
        composer.press("Enter")  # type: ignore[union-attr]

    def _assistant_locator(self, page: Page):
        for selector in ASSISTANT_SELECTORS:
            locator = page.locator(selector)
            try:
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        return page.locator(ASSISTANT_SELECTORS[0])

    def _assistant_count(self, page: Page) -> int:
        locator = self._assistant_locator(page)
        try:
            return locator.count()
        except Exception:
            return 0

    def _wait_for_last_assistant_message(self, page: Page, *, previous_count: int) -> str:
        deadline = time.monotonic() + self.config.chatgpt_timeout_s
        last_text = ""
        stable_polls = 0
        while time.monotonic() < deadline:
            locator = self._assistant_locator(page)
            count = locator.count()
            if count > previous_count:
                current = locator.nth(count - 1).inner_text().strip()
                if current and current == last_text and not self._has_stop_button(page):
                    stable_polls += 1
                    if stable_polls >= 3:
                        return current
                else:
                    last_text = current
                    stable_polls = 0
            page.wait_for_timeout(2_000)
        raise RuntimeError("Timed out waiting for ChatGPT response.")

    def _has_stop_button(self, page: Page) -> bool:
        for selector in STOP_BUTTON_SELECTORS:
            try:
                if page.locator(selector).first.is_visible(timeout=500):  # type: ignore[call-arg]
                    return True
            except Exception:
                continue
        return False
