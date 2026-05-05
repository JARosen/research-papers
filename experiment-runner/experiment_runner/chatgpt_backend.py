from __future__ import annotations

import time
from dataclasses import dataclass

from .config import RunnerConfig

try:
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver import ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.remote.webdriver import WebDriver
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except Exception:  # pragma: no cover - import checked at runtime
    webdriver = None  # type: ignore[assignment]
    TimeoutException = Exception  # type: ignore[assignment]
    ChromeOptions = object  # type: ignore[assignment,misc]
    By = Keys = WebDriver = WebDriverWait = EC = object  # type: ignore[assignment,misc]


ASSISTANT_SELECTORS = [
    (By.CSS_SELECTOR, "[data-message-author-role='assistant']"),
    (By.CSS_SELECTOR, "article [data-message-author-role='assistant']"),
    (By.CSS_SELECTOR, "main article"),
]
COMPOSER_SELECTORS = [
    (By.CSS_SELECTOR, "#prompt-textarea"),
    (By.CSS_SELECTOR, "textarea"),
    (By.CSS_SELECTOR, "div[contenteditable='true']#prompt-textarea"),
    (By.CSS_SELECTOR, "div[contenteditable='true']"),
]
NEW_CHAT_SELECTORS = [
    (By.CSS_SELECTOR, "a[href='/']"),
    (By.XPATH, "//button[contains(., 'New chat')]"),
    (By.CSS_SELECTOR, "[data-testid='new-chat-button']"),
]
SEND_BUTTON_SELECTORS = [
    (By.CSS_SELECTOR, "button[data-testid='send-button']"),
    (By.XPATH, "//button[contains(@aria-label, 'Send')]"),
]
STOP_BUTTON_SELECTORS = [
    (By.CSS_SELECTOR, "button[data-testid='stop-button']"),
    (By.XPATH, "//button[contains(@aria-label, 'Stop')]"),
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
        if webdriver is None:
            raise RuntimeError("Selenium is not installed. Run `pip install .` first.")

    def bootstrap_login(self) -> None:
        driver = self._connect_driver()
        try:
            print(f"Opening ChatGPT at {self.config.chatgpt_url} ...")
            driver.get(self.config.chatgpt_url)
            print(
                "Attached to existing Chrome over the remote debugging port. "
                "Verify you are logged in, then press Enter here to continue."
            )
            input()
        finally:
            self._detach_driver(driver)

    def run_repeated_trials(self, prompts: list[str]) -> list[ChatRunResult]:
        driver = self._connect_driver()
        results: list[ChatRunResult] = []
        try:
            print(f"Opening ChatGPT at {self.config.chatgpt_url} ...")
            driver.get(self.config.chatgpt_url)
            for index, prompt in enumerate(prompts, start=1):
                print(f"[chatgpt] starting repeated run {index}/{len(prompts)}")
                manual_temporary = self._start_new_chat(driver)
                if not manual_temporary:
                    self._ensure_temporary_chat(driver)
                self._wait_for_composer_ready(driver)
                results.append(self._submit_prompt(driver, prompt))
                print(
                    f"[chatgpt] completed repeated run {index}/{len(prompts)} "
                    f"in {results[-1].duration_s:.2f}s"
                )
            return results
        finally:
            self._detach_driver(driver)

    def run_initial_and_update(self, initial_prompt: str, update_prompt: str) -> tuple[ChatRunResult, ChatRunResult]:
        driver = self._connect_driver()
        try:
            print(f"Opening ChatGPT at {self.config.chatgpt_url} ...")
            driver.get(self.config.chatgpt_url)
            print("[chatgpt] starting initial run for update scenario")
            manual_temporary = self._start_new_chat(driver)
            if not manual_temporary:
                self._ensure_temporary_chat(driver)
            self._wait_for_composer_ready(driver)
            initial = self._submit_prompt(driver, initial_prompt)
            print(f"[chatgpt] completed initial run for update scenario in {initial.duration_s:.2f}s")
            print("[chatgpt] submitting upstream update prompt")
            updated = self._submit_prompt(driver, update_prompt)
            print(f"[chatgpt] completed updated run in {updated.duration_s:.2f}s")
            return initial, updated
        finally:
            self._detach_driver(driver)

    def _connect_driver(self) -> WebDriver:
        print("Attaching Selenium to existing Chrome at 127.0.0.1:9222 ...")
        options = ChromeOptions()
        options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        return webdriver.Chrome(options=options)

    def _detach_driver(self, driver: WebDriver) -> None:
        try:
            driver.service.stop()
        except Exception:
            pass

    def _start_new_chat(self, driver: WebDriver) -> bool:
        print("[chatgpt] opening a new chat")
        for by, selector in NEW_CHAT_SELECTORS:
            try:
                element = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, selector)))
                element.click()
                time.sleep(1.5)
                print(f"[chatgpt] clicked new chat via selector {selector!r}")
                return False
            except Exception:
                continue
        print("[chatgpt] could not find explicit new chat button")
        input("[chatgpt] Open a new Temporary chat manually in Chrome, then press Enter here to continue...")
        time.sleep(1.0)
        return True

    def _ensure_temporary_chat(self, driver: WebDriver) -> None:
        input("[chatgpt] Enable Temporary manually in Chrome, then press Enter here to continue...")
        time.sleep(1.0)

    def _wait_for_composer_ready(self, driver: WebDriver) -> None:
        composer = self._find_composer(driver)
        WebDriverWait(driver, 10).until(lambda _: composer.is_displayed() and composer.is_enabled())
        time.sleep(0.5)

    def _submit_prompt(self, driver: WebDriver, prompt: str) -> ChatRunResult:
        previous_count = self._assistant_count(driver)
        composer = self._find_composer(driver)
        start = time.perf_counter()
        print(f"[chatgpt] submitting prompt ({len(prompt)} chars)")
        self._set_composer_text(driver, composer, prompt)
        self._click_send(driver, composer)
        print("[chatgpt] prompt submitted, waiting for assistant response")
        final_text = self._wait_for_last_assistant_message(driver, previous_count=previous_count)
        duration_s = time.perf_counter() - start
        print(f"[chatgpt] received assistant response ({len(final_text)} chars)")
        return ChatRunResult(
            prompt=prompt,
            final_text=final_text,
            duration_s=duration_s,
            conversation_url=driver.current_url,
        )

    def _find_composer(self, driver: WebDriver):
        last_exc = None
        for by, selector in COMPOSER_SELECTORS:
            try:
                return WebDriverWait(driver, 10).until(EC.presence_of_element_located((by, selector)))
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(f"Could not find ChatGPT composer: {last_exc!r}")

    def _click_send(self, driver: WebDriver, composer) -> None:
        for by, selector in SEND_BUTTON_SELECTORS:
            try:
                button = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((by, selector)))
                button.click()
                print(f"[chatgpt] clicked send via selector {selector!r}")
                return
            except Exception:
                continue
        print("[chatgpt] send button not found, falling back to Enter key")
        composer.send_keys(Keys.ENTER)

    def _set_composer_text(self, driver: WebDriver, composer, prompt: str) -> None:
        print("[chatgpt] filling composer directly to avoid newline submission issues")
        driver.execute_script(
            """
            const el = arguments[0];
            const text = arguments[1];
            el.focus();
            if (el.tagName === 'TEXTAREA') {
              el.value = text;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return;
            }
            if (el.isContentEditable) {
              el.textContent = text;
              el.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                inputType: 'insertText',
                data: text
              }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return;
            }
            el.value = text;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            """,
            composer,
            prompt,
        )
        time.sleep(0.5)

    def _assistant_elements(self, driver: WebDriver):
        for by, selector in ASSISTANT_SELECTORS:
            elements = driver.find_elements(by, selector)
            if elements:
                return elements
        return []

    def _assistant_count(self, driver: WebDriver) -> int:
        return len(self._assistant_elements(driver))

    def _wait_for_last_assistant_message(self, driver: WebDriver, *, previous_count: int) -> str:
        deadline = time.monotonic() + self.config.chatgpt_timeout_s
        last_text = ""
        stable_polls = 0
        while time.monotonic() < deadline:
            elements = self._assistant_elements(driver)
            if len(elements) > previous_count:
                current = elements[-1].text.strip()
                if current and current == last_text and not self._has_stop_button(driver):
                    stable_polls += 1
                    if stable_polls >= 3:
                        return current
                else:
                    last_text = current
                    stable_polls = 0
            time.sleep(2)
        raise RuntimeError("Timed out waiting for ChatGPT response.")

    def _has_stop_button(self, driver: WebDriver) -> bool:
        for by, selector in STOP_BUTTON_SELECTORS:
            try:
                elements = driver.find_elements(by, selector)
                if any(element.is_displayed() for element in elements):
                    return True
            except Exception:
                continue
        return False
