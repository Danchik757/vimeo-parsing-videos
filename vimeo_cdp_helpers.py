"""Helpers for Vimeo download flows in SeleniumBase UC/CDP mode."""

import html
import re
import time
from contextlib import suppress

from selenium.webdriver.common.by import By


DOWNLOAD_BUTTON_SELECTORS = [
    "button[aria-label='Download button']",
    ".chakra-stack.css-tistzx button[aria-label*='Download']",
    ".chakra-stack button[aria-label*='Download']",
    "a[aria-label*='Download']",
    "[data-testid*='download']",
    "[role='button'][aria-label*='Download']",
    "button[aria-label*='Download']",
    "button[title*='Download']",
]


def _sleep(sb, seconds):
    if hasattr(sb, "sleep"):
        sb.sleep(seconds)
    else:
        time.sleep(seconds)


def _normalize_text(value):
    return " ".join((value or "").split())


def _matches_download_href(href):
    href = str(href or "")
    return bool(
        href
        and (
            "progressive_redirect/download" in href
            or "/download/" in href
            or "source=1" in href
            or "filename=" in href
            or ".mp4" in href.lower()
            or ".mov" in href.lower()
            or ".m4v" in href.lower()
        )
    )


def _extract_element_text(element):
    text = ""
    with suppress(Exception):
        text = element.text
    return _normalize_text(text)


def _extract_download_links_from_html(page_html):
    html_text = html.unescape(str(page_html or ""))
    pattern = re.compile(
        r"https://[^\"'\\s<>]+(?:progressive_redirect/download[^\"'\\s<>]*|/download/[^\"'\\s<>]*|[^\"'\\s<>]*[?&]source=1[^\"'\\s<>]*)",
        re.IGNORECASE,
    )
    results = []
    seen = set()
    for href in pattern.findall(html_text):
        href = href.replace("&amp;", "&")
        if href in seen or not _matches_download_href(href):
            continue
        seen.add(href)
        results.append(
            {
                "id": "html-fallback",
                "text": "html fallback",
                "href": href,
            }
        )
    return results


def modal_looks_like_transcript(page_html):
    html_text = html.unescape(str(page_html or "")).lower()
    return "download transcript" in html_text or "captions.vtt" in html_text


def _extract_download_options_from_scope(scope_element):
    options = []

    rows = []
    with suppress(Exception):
        rows = scope_element.query_selector_all("[id^='download-file']")

    for row in rows or []:
        link = None
        with suppress(Exception):
            link = row.query_selector("a[href]")
        if not link:
            continue
        href = ""
        with suppress(Exception):
            href = link.get_attribute("href") or ""
        if not _matches_download_href(href):
            continue
        options.append(
            {
                "id": getattr(row, "tag_name", "") or "",
                "text": _extract_element_text(row) or _extract_element_text(link),
                "href": href,
            }
        )

    if options:
        return options

    links = []
    with suppress(Exception):
        links = scope_element.query_selector_all("a[href]")

    for link in links or []:
        href = ""
        with suppress(Exception):
            href = link.get_attribute("href") or ""
        if not _matches_download_href(href):
            continue
        options.append(
            {
                "id": "",
                "text": _extract_element_text(link),
                "href": href,
            }
        )

    return options


def _iter_player_iframe_elements(sb):
    driver = getattr(sb, "driver", None)
    if driver is None:
        return []

    with suppress(Exception):
        driver.switch_to.default_content()

    with suppress(Exception):
        return driver.find_elements(By.CSS_SELECTOR, "iframe[src*='player.vimeo.com/video/']")
    return []


def _click_download_button_in_current_frame(driver, logger=None):
    last_error = None

    for selector in DOWNLOAD_BUTTON_SELECTORS:
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception as exc:
            last_error = exc
            continue

        for button in buttons:
            try:
                if not button.is_displayed():
                    continue
            except Exception:
                pass

            aria_label = ""
            with suppress(Exception):
                aria_label = button.get_attribute("aria-label") or ""

            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", button
                )
            except Exception:
                pass

            try:
                button.click()
            except Exception:
                driver.execute_script("arguments[0].click();", button)

            if logger:
                logger.info(
                    "Found and clicked download button inside player iframe via %s (aria-label='%s')",
                    selector,
                    aria_label or "(no aria-label)",
                )
            return {
                "method": "iframe",
                "selector": selector,
                "aria_label": aria_label,
            }

    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, "button, a, [role='button']")
    except Exception as exc:
        last_error = exc
        candidates = []

    for button in candidates:
        aria_label = ""
        title = ""
        text = ""
        with suppress(Exception):
            aria_label = button.get_attribute("aria-label") or ""
        with suppress(Exception):
            title = button.get_attribute("title") or ""
        with suppress(Exception):
            text = button.text or ""
        haystack = _normalize_text(" ".join([aria_label, title, text])).lower()
        if "download" not in haystack:
            continue
        try:
            if not button.is_displayed():
                continue
        except Exception:
            pass
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", button
            )
        except Exception:
            pass
        try:
            button.click()
        except Exception:
            driver.execute_script("arguments[0].click();", button)
        if logger:
            logger.info(
                "Found and clicked download button inside player iframe via generic scan"
            )
        return {
            "method": "iframe",
            "selector": "iframe-element[text*=download]",
            "aria_label": aria_label,
        }

    raise RuntimeError(f"Download button not found inside player iframe: {last_error}")


def _score_download_option(option):
    text = " ".join(
        str(option.get(key, "")) for key in ("text", "quality", "rendition")
    ).lower()
    href = str(option.get("href", "")).lower()

    score = 0
    if "original" in text or "source" in text:
        score += 100000

    for source in (text, href):
        match = re.search(r"(\d{3,4})p", source)
        if match:
            score = max(score, int(match.group(1)))
            break

    height = option.get("height")
    width = option.get("width")
    if isinstance(height, (int, float)):
        score = max(score, int(height))
    elif isinstance(width, (int, float)):
        score = max(score, int(width))

    if ".mp4" in href:
        score += 5

    return score


def choose_best_download_option(options):
    valid_options = []
    for option in options or []:
        if not isinstance(option, dict):
            continue
        href = option.get("href")
        if isinstance(href, str) and href:
            valid_options.append(option)
    if not valid_options:
        return None
    return max(valid_options, key=_score_download_option)


def extract_best_api_download(metadata):
    if not isinstance(metadata, dict):
        return None

    api_options = []
    for item in metadata.get("download") or []:
        if not isinstance(item, dict):
            continue
        href = item.get("link")
        if not isinstance(href, str) or not href:
            continue
        api_options.append(
            {
                "href": href,
                "text": " ".join(
                    str(item.get(key, ""))
                    for key in ("quality", "rendition", "type")
                    if item.get(key)
                ).strip(),
                "quality": item.get("quality"),
                "rendition": item.get("rendition"),
                "width": item.get("width"),
                "height": item.get("height"),
            }
        )

    return choose_best_download_option(api_options)


def click_download_button(sb, timeout=10, logger=None):
    last_error = None

    for index, selector in enumerate(DOWNLOAD_BUTTON_SELECTORS):
        per_selector_timeout = timeout if index == 0 else min(1.5, timeout)
        try:
            button = sb.wait_for_query_selector(selector, timeout=per_selector_timeout)
            with suppress(Exception):
                button.scroll_into_view()
            button.click()
            aria_label = ""
            with suppress(Exception):
                aria_label = button.get_attribute("aria-label") or ""
            if logger:
                logger.info(
                    "Found and clicked download button via %s (aria-label='%s')",
                    selector,
                    aria_label or "(no aria-label)",
                )
            return {
                "method": "cdp",
                "selector": selector,
                "aria_label": aria_label,
            }
        except Exception as exc:
            last_error = exc

    if hasattr(sb, "cdp"):
        try:
            buttons = sb.cdp.select_all("button, a, [role='button']", timeout=1)
        except Exception:
            buttons = []
        for button in buttons:
            aria_label = ""
            title = ""
            with suppress(Exception):
                aria_label = button.get_attribute("aria-label") or ""
            with suppress(Exception):
                title = button.get_attribute("title") or ""
            text = _extract_element_text(button)
            haystack = " ".join([aria_label, title, text]).lower()
            if "download" not in haystack:
                continue
            with suppress(Exception):
                button.scroll_into_view()
            try:
                button.click()
            except Exception:
                with suppress(Exception):
                    sb.execute_script("arguments[0].click();", button)
            if logger:
                logger.info(
                    "Found and clicked download button via generic CDP element scan"
                )
            return {
                "method": "cdp",
                "selector": "element[text*=download]",
                "aria_label": aria_label,
            }

    raise RuntimeError(
        "Download button not found via CDP/querySelector fallback: %s"
        % last_error
    )


def click_download_button_in_player_iframe(sb, timeout=10, logger=None):
    deadline = time.time() + timeout
    last_error = None

    while time.time() < deadline:
        for iframe in _iter_player_iframe_elements(sb):
            driver = getattr(sb, "driver", None)
            if driver is None:
                continue
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(iframe)
                return _click_download_button_in_current_frame(driver, logger=logger)
            except Exception as exc:
                last_error = exc
            finally:
                with suppress(Exception):
                    driver.switch_to.default_content()
        _sleep(sb, 0.5)

    raise RuntimeError(
        "Download button not found inside Vimeo player iframe: %s" % last_error
    )


def extract_best_modal_download(sb, timeout=10, logger=None):
    deadline = time.time() + timeout
    modal_seen = False
    last_options = []

    while time.time() < deadline:
        scope = "page"
        scope_element = None

        for selector in ("section[aria-modal='true']", "[role='dialog'][aria-modal='true']"):
            with suppress(Exception):
                scope_element = sb.wait_for_query_selector(selector, timeout=1)
                scope = "modal"
                modal_seen = True
                break

        if scope_element is None and hasattr(sb, "cdp"):
            with suppress(Exception):
                scope_element = sb.cdp.select("body", timeout=1)

        if scope_element is not None:
            last_options = _extract_download_options_from_scope(scope_element)
            best_option = choose_best_download_option(last_options)
            if best_option:
                if logger:
                    logger.info(
                        "Selected download option from %s: %s",
                        scope,
                        best_option.get("text") or best_option.get("href"),
                    )
                return best_option

        with suppress(Exception):
            html_options = _extract_download_links_from_html(sb.get_page_source())
            if html_options:
                last_options = html_options
                best_option = choose_best_download_option(last_options)
                if best_option:
                    if logger:
                        logger.info(
                            "Selected download option from html fallback: %s",
                            best_option.get("text") or best_option.get("href"),
                        )
                    return best_option
        _sleep(sb, 0.5)

    if modal_seen:
        raise RuntimeError("No download option found in modal")
    if last_options:
        raise RuntimeError("No usable download links found after clicking Download")
    raise RuntimeError("Download modal not found")


def extract_best_player_iframe_download(sb, timeout=10, logger=None):
    deadline = time.time() + timeout
    last_options = []
    transcript_seen = False

    while time.time() < deadline:
        for iframe in _iter_player_iframe_elements(sb):
            driver = getattr(sb, "driver", None)
            if driver is None:
                continue
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(iframe)
                frame_html = driver.page_source
            except Exception:
                with suppress(Exception):
                    driver.switch_to.default_content()
                continue
            finally:
                with suppress(Exception):
                    driver.switch_to.default_content()

            if modal_looks_like_transcript(frame_html):
                transcript_seen = True

            html_options = _extract_download_links_from_html(frame_html)
            if html_options:
                last_options = html_options
                best_option = choose_best_download_option(last_options)
                if best_option:
                    if logger:
                        logger.info(
                            "Selected download option from player iframe html fallback: %s",
                            best_option.get("text") or best_option.get("href"),
                        )
                    return best_option
        _sleep(sb, 0.5)

    if transcript_seen:
        raise RuntimeError("Player iframe exposed transcript download only")
    if last_options:
        raise RuntimeError("No usable download links found in player iframe")
    raise RuntimeError("Download controls not found in player iframe")
