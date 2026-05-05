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
        self.config.chatgpt_profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.config.chatgpt_profile_dir),
                headless=False,
            )
            page = context.new_page()
            page.goto(self.config.chatgpt_url)
            print("Complete ChatGPT login in the opened browser, then press Enter here to continue.")
            input()
            context.storage_state(path=str(self.config.chatgpt_profile_dir / "storage_state.json"))
            context.close()

    def run_repeated_trials(self, prompts: list[str]) -> list[ChatRunResult]:
        results: list[ChatRunResult] = []
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.config.chatgpt_profile_dir),
                headless=False,
            )
            page = context.new_page()
            page.goto(self.config.chatgpt_url)
            for prompt in prompts:
                self._start_new_chat(page)
                results.append(self._submit_prompt(page, prompt))
            context.close()
        return results

    def run_initial_and_update(self, initial_prompt: str, update_prompt: str) -> tuple[ChatRunResult, ChatRunResult]:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.config.chatgpt_profile_dir),
                headless=False,
            )
            page = context.new_page()
            page.goto(self.config.chatgpt_url)
            self._start_new_chat(page)
            initial = self._submit_prompt(page, initial_prompt)
            updated = self._submit_prompt(page, update_prompt)
            context.close()
        return initial, updated

    def _start_new_chat(self, page: Page) -> None:
        for selector in NEW_CHAT_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(timeout=2_000)
                locator.click()
                page.wait_for_timeout(1_500)
                return
            except Exception:
                continue
        page.goto(self.config.chatgpt_url)
        page.wait_for_timeout(1_500)

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
