#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


def main() -> None:
    base = os.environ["SIRENFINDER_URL"].rstrip("/")
    key = Path("private/sirenfinder-access.txt").read_text(encoding="utf-8").strip()
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1440,960")
    options.add_argument("--use-gl=swiftshader")
    options.add_argument("--enable-webgl")
    options.add_argument("--ignore-gpu-blocklist")
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(180)
    try:
        driver.get(f"{base}#key={key}")
        wait = WebDriverWait(driver, 240)
        wait.until(lambda d: d.find_element(By.ID, "pinCount").text.replace(",", "").isdigit())
        wait.until(lambda d: "sirens loaded" in d.find_element(By.ID, "statusText").text.lower())
        pin_text = driver.find_element(By.ID, "pinCount").text
        source_text = driver.find_element(By.ID, "sourceCount").text
        canvas_count = driver.execute_script("return document.querySelectorAll('.maplibregl-canvas').length")
        search = driver.find_element(By.ID, "search")
        if search.get_attribute("disabled"):
            raise RuntimeError("Search remained disabled")
        search.send_keys("Model 2")
        wait.until(lambda d: d.find_element(By.ID, "resultCount").text not in ("0", pin_text))
        result_text = driver.find_element(By.ID, "resultCount").text
        driver.save_screenshot("sirenfinder-live.png")
        serious = [entry for entry in driver.get_log("browser") if entry.get("level") == "SEVERE" and "tile.openstreetmap.org" not in entry.get("message", "")]
        report = {
            "url": base,
            "pinCount": pin_text,
            "sourceCount": source_text,
            "searchResultCount": result_text,
            "mapCanvasCount": canvas_count,
            "seriousConsoleErrors": serious,
        }
        Path("sirenfinder-live-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        if int(pin_text.replace(",", "")) < 80000:
            raise RuntimeError(f"Unexpected pin count: {pin_text}")
        if canvas_count < 1:
            raise RuntimeError("Map canvas was not created")
        if serious:
            raise RuntimeError(f"Serious browser errors: {serious[:3]}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
