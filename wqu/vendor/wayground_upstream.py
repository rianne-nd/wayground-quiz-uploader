#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WayGround CLI —— 讓 AI 助理控制 WayGround (Quizizz) 進行自動化出題。
專屬助手: 基於 kahoot-cli 架構簡化改編，專注於 WayGround (Quizizz) 平台 Playwright 自動化出題與 Session 管理。
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# 強制設定標準輸出/錯誤流的編碼為 utf-8 (防止 Windows CP950 終端機 Emoji 報錯)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ---- 路徑與常數設定 ----
CONFIG_DIR = Path.home() / ".wayground"
STATE_FILE = CONFIG_DIR / "state.json"                # 登入後的 cookies 儲存檔案
CHROME_LOGIN_FILE = CONFIG_DIR / "chrome-login.json"  # 記錄啟動的專用 Chrome 連線資訊
CHROME_LOGIN_PROFILE = CONFIG_DIR / "chrome-login-profile"
DEFAULT_CDP_PORT = 9335
DEBUG_DIR = Path(__file__).resolve().parent / "debug"
PRIMARY_URL = "https://wayground.com"
FALLBACK_URL = "https://quizizz.com"
ADMIN_PATH = "/explore/admin"

def die(msg: str, code: int = 1):
    """列印錯誤訊息並結束程式。"""
    print(f"[錯誤] {msg}", file=sys.stderr)
    sys.exit(code)

def _need_playwright():
    """延遲載入 Playwright，並在缺少時給予友善提示。"""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        die("尚未安裝 Playwright。請執行:\n"
            "    python -m pip install -r requirements.txt\n"
            "    python -m playwright install chromium\n"
            "或執行一鍵安裝腳本: .\\setup.ps1")

def _find_chrome(chrome_path: str | None = None) -> Path:
    """尋找系統中的 Google Chrome 執行檔路徑。"""
    if chrome_path:
        candidate = Path(chrome_path).expanduser().resolve()
        if candidate.is_file():
            return candidate
        die(f"找不到指定的 Chrome: {candidate}", code=4)
    
    # 搜尋預設名稱
    for name in ("chrome.exe", "chrome", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
            
    # 常見安裝路徑 (Windows / macOS / Linux)
    candidates = []
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        candidates.extend((Path("/usr/bin/google-chrome"), Path("/usr/bin/google-chrome-stable")))
        
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
            
    die("找不到 Google Chrome。請安裝 Chrome，或用 --chrome-path 指定執行檔路徑。", code=4)

def _port_is_available(port: int) -> bool:
    """確認本機除錯埠尚未被其他工具占用。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False

def _resolve_grab_session_cdp_url(explicit_url: str | None) -> str:
    """決定 CDP 連線網址，優先使用上次 Chrome 登入紀錄。"""
    if explicit_url:
        return explicit_url.rstrip("/")
    if not CHROME_LOGIN_FILE.is_file():
        die("找不到本工具啟動的 Chrome 紀錄。請先執行:\n"
            "    python wayground.py chrome-login\n"
            "完成登入後，再執行 python wayground.py grab-session。", code=4)
    try:
        metadata = json.loads(CHROME_LOGIN_FILE.read_text(encoding="utf-8"))
        port = int(metadata["port"])
    except Exception:
        die(f"Chrome 登入紀錄已損壞: {CHROME_LOGIN_FILE}\n請重新執行 python wayground.py chrome-login。", code=4)
        
    return f"http://127.0.0.1:{port}"

def cmd_doctor(args):
    """診斷本機環境與登入狀態。"""
    print("========================================")
    print(" 🔍 WayGround CLI 環境診斷報告")
    print("========================================")

    # 1. Python 版本
    print(f"[OK] Python 版本: {sys.version.split()[0]}")

    # 2. Playwright 模組
    try:
        import importlib.metadata
        pw_ver = importlib.metadata.version("playwright")
        print(f"[OK] Playwright 模組已安裝 (v{pw_ver})")
    except Exception:
        try:
            import playwright
            print("[OK] Playwright 模組已安裝")
        except ImportError:
            print("[X]  Playwright 模組未安裝")

    # 3. Google Chrome
    try:
        chrome_exe = _find_chrome(getattr(args, "chrome_path", None))
        print(f"[OK] Google Chrome 執行檔: {chrome_exe}")
    except SystemExit:
        print("[X]  找不到 Google Chrome")

    # 4. Session 狀態
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            cookies = state.get("cookies", [])
            print(f"[OK] 找到已儲存的 Session 憑證: {STATE_FILE} (包含 {len(cookies)} 個 cookies)")
        except Exception:
            print(f"[X]  Session 憑證檔案損壞: {STATE_FILE}")
    else:
        print(f"[!]  尚未建立 Session 憑證 ({STATE_FILE})。請執行 chrome-login 與 grab-session。")

    print("========================================")

def cmd_chrome_login(args):
    """開啟原生 Google Chrome 視窗（帶 CDP Port 9335），繞過 Google 機器人安全檢測。"""
    chrome_exe = _find_chrome(getattr(args, "chrome_path", None))
    port = getattr(args, "port", None) or DEFAULT_CDP_PORT

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    profile_dir = CHROME_LOGIN_PROFILE
    profile_dir.mkdir(parents=True, exist_ok=True)

    login_url = f"{PRIMARY_URL}/admin"
    cmd = [
        str(chrome_exe),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        login_url
    ]

    print(f"[資訊] 正在開啟原生 Google Chrome 登入視窗 (Port: {port})...")
    print(f"[說明] 此視窗為真實原生 Chrome，可正常進行 Google / 第三方帳號安全登入。")
    print(f"[說明] 完成登入後，請執行 `python wayground.py grab-session` 或點擊『2-儲存憑證.bat』。")

    try:
        proc = subprocess.Popen(cmd)
        pid = proc.pid
    except Exception as e:
        die(f"無法啟動 Chrome: {e}")

    meta = {
        "port": port,
        "pid": pid,
        "profile_dir": str(profile_dir),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    CHROME_LOGIN_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[成功] 原生 Chrome 視窗已開啟。")

def cmd_grab_session(args):
    """連線至 CDP Chrome 視窗並將登入憑證擷取儲存至 state.json。"""
    sync_playwright = _need_playwright()
    cdp_url = _resolve_grab_session_cdp_url(args.cdp_url)

    print(f"[資訊] 正在連線至 Chrome ({cdp_url})...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            die(f"無法連線至 CDP {cdp_url}。請確認 Chrome 視窗仍在開啟狀態。\n詳細錯誤: {e}")

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        storage_state = context.storage_state()
        STATE_FILE.write_text(json.dumps(storage_state, ensure_ascii=False, indent=2), encoding="utf-8")
        
        cookie_count = len(storage_state.get("cookies", []))
        print(f"[成功] Session 憑證已成功寫入 {STATE_FILE} (包含 {cookie_count} 個 cookies)。")

def cmd_check(args):
    """載入 state.json 並造訪 WayGround 驗證登入狀態。"""
    if not STATE_FILE.is_file():
        die(f"找不到 Session 憑證檔案 ({STATE_FILE})。\n請先執行 chrome-login 與 grab-session。")

    sync_playwright = _need_playwright()
    print("[資訊] 正在載入 Session 憑證進行 WayGround 登入驗證...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.no_headless)
        context = browser.new_context(storage_state=str(STATE_FILE))
        page = context.new_page()

        try:
            page.goto(f"{PRIMARY_URL}/admin", timeout=15000, wait_until="domcontentloaded")
            time.sleep(3)

            # 檢查網頁標題或 URL 是否在管理員頁面
            current_url = page.url
            if "admin" in current_url or "login" not in current_url:
                print(f"[成功] [OK] 登入有效！(目前網址: {current_url})")
            else:
                print(f"[警告] [失效] 登入憑證可能已過期 (重定向至: {current_url})。")
                print("請重新執行 python wayground.py chrome-login 手動登入。")
        except Exception as e:
            print(f"[錯誤] 驗證時發生異常: {e}")
        finally:
            browser.close()

def _validate_quiz_json(content_path: Path) -> dict:
    """解析並校驗題目 JSON 格式規格。"""
    if not content_path.is_file():
        die(f"找不到指定的 JSON 題目檔案: {content_path}")

    try:
        data = json.loads(content_path.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"JSON 語法格式錯誤 ({content_path}): {e}")

    if "title" not in data or not data["title"].strip():
        die("JSON 必須包含非空的 'title' 欄位。")

    questions = data.get("questions", [])
    if not isinstance(questions, list) or len(questions) == 0:
        die("JSON 的 'questions' 必須是包含至少一個題目的列表。")

    supported_types = {"quiz", "multiple_choice", "fill_in_the_blank", "type_answer", "checkbox", "poll", "open_ended"}

    for idx, q in enumerate(questions, start=1):
        if "question" not in q or not q["question"].strip():
            die(f"第 {idx} 題缺少 'question' 題幹欄位。")
        if not isinstance(q, dict):
            die(f"第 {idx} 題必須是 JSON 物件。")

        q_type = q.get("type", "quiz")
        if q_type not in supported_types:
            die(f"第 {idx} 題不支援的題型: '{q_type}'。支援題型: {supported_types}")

        answers = q.get("answers", [])
        if not isinstance(answers, list):
            die(f"第 {idx} 題的 'answers' 必須是陣列。")
        if q_type in {"quiz", "multiple_choice", "checkbox", "poll"} and not 2 <= len(answers) <= 4:
            die(f"第 {idx} 題的選項數量必須介於 2 至 4。")
        if q_type in {"fill_in_the_blank", "type_answer"} and len(answers) < 1:
            die(f"第 {idx} 題至少需要一個正確答案。")
        if q_type == "checkbox":
            correct = q.get("correct")
            if not isinstance(correct, list) or not correct:
                die(f"第 {idx} 題 checkbox 的 'correct' 必須是非空索引陣列。")
            if any(not isinstance(i, int) or i < 0 or i >= len(answers) for i in correct):
                die(f"第 {idx} 題 checkbox 的正確答案索引超出範圍。")
        elif q_type in {"quiz", "multiple_choice", "fill_in_the_blank", "type_answer"}:
            correct = q.get("correct", 0)
            if not isinstance(correct, int) or correct < 0 or correct >= len(answers):
                die(f"第 {idx} 題的 'correct' 必須是有效的答案索引。")

    return data


def _first_visible(page, selectors):
    """回傳第一個可見且可操作的 locator；找不到時回傳 None。"""
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def _click_text(page, texts, timeout=4000):
    """以按鈕文字尋找操作，兼容英文與中文介面。"""
    for text in texts:
        candidates = [
            page.get_by_role("button", name=re.compile(re.escape(text), re.I)),
            page.get_by_text(re.compile(re.escape(text), re.I)),
        ]
        for locator in candidates:
            try:
                if locator.count() and locator.first.is_visible():
                    locator.first.click(timeout=timeout)
                    return True
            except Exception:
                continue
    return False


def _fill_first(page, selectors, value, field_name):
    locator = _first_visible(page, selectors)
    if not locator:
        raise RuntimeError(f"找不到{field_name}輸入欄位。請以 --no-headless 執行並更新 selector。")
    locator.fill(str(value))
    return locator


def _open_new_assessment(page):
    """依 WayGround 目前的建立流程取得真正的 quiz id。"""
    # 先暖身管理頁，再進入建立分頁；WayGround 首次直接載入 tab=create
    # 時偶爾會先顯示登入頁，稍後才完成既有 Session 的 hydration。
    page.goto(f"{PRIMARY_URL}{ADMIN_PATH}", timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(4500)
    page.goto(f"{PRIMARY_URL}{ADMIN_PATH}?tab=create", timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(4500)
    auth_heading = page.locator('[data-testid="auth-modal-welcome-title"]')
    if auth_heading.count() and auth_heading.first.is_visible():
        raise RuntimeError("WayGround 建立頁目前顯示登入畫面，請重新擷取登入 Session。")
    continue_button = page.locator('[data-testid="continue-button"]')
    if continue_button.count() and continue_button.first.is_visible():
        raise RuntimeError("WayGround 仍顯示學校／組織設定，請先完成設定後再重試。")
    cards = page.locator('[data-testid="create-resource-card"]')
    try:
        cards.first.wait_for(state="visible", timeout=15000)
    except Exception:
        raise RuntimeError("找不到 WayGround 的建立資源卡片。")
    cards.first.click()
    page.wait_for_timeout(1200)
    options = page.locator('[data-testid="resource-creation-option-selector"]')
    try:
        options.nth(2).wait_for(state="visible", timeout=15000)
    except Exception:
        raise RuntimeError("找不到「從頭開始」選項，建立評估視窗可能尚未完成載入。")
    options.nth(2).click()
    page.wait_for_timeout(3000)
    if "/activity/admin/quiz/" not in page.url:
        raise RuntimeError(f"未進入新的測驗編輯器，當前網址: {page.url}")


def _set_quiz_title(page, title):
    button = page.locator('[data-testid="edit-name-button"]')
    if not button.count():
        raise RuntimeError("找不到測驗名稱按鈕。")
    button.first.click()
    page.wait_for_timeout(300)
    title_input = _first_visible(page, ['[data-testid="quiz-name-input"]', 'input[placeholder*="名稱"], input[placeholder*="name" i]', 'input[type="text"]'])
    if not title_input:
        raise RuntimeError("找不到測驗名稱輸入欄位。")
    title_input.click()
    title_input.press("Control+A")
    title_input.type(title, delay=10)
    if title_input.input_value() != title:
        raise RuntimeError("測驗標題輸入值未成功寫入。")
    title_input.press("Tab")
    page.wait_for_timeout(300)
    save_settings = page.locator('[data-testid="quiz-settings-modal-primary-button"]')
    if save_settings.count():
        save_settings.click(force=True)
    else:
        title_input.press("Enter")
    page.wait_for_timeout(500)
    modal = page.locator('[data-testid="quiz-settings-modal"]')
    if modal.count() and modal.first.is_visible():
        # WayGround 某些版本保存設定後不會自動收起視窗；關閉它才能繼續編輯題目。
        close_button = modal.locator('[data-testid="close-button"]')
        if close_button.count():
            close_button.last.click(force=True)
            page.wait_for_timeout(500)


def _close_optional_panels(page):
    """關閉 WayGround 可能自動開啟的資源搜尋側欄。"""
    settings_modal = page.locator('[data-testid="quiz-settings-modal"]')
    if settings_modal.count() and settings_modal.first.is_visible():
        save_settings = page.locator('[data-testid="quiz-settings-modal-primary-button"]')
        if save_settings.count() and save_settings.first.is_visible():
            save_settings.first.click(force=True)
            page.wait_for_timeout(500)
        if settings_modal.count() and settings_modal.first.is_visible():
            close_button = settings_modal.locator('[data-testid="close-button"]')
            if close_button.count():
                close_button.last.click(force=True)
                page.wait_for_timeout(500)
    close_side_sheet = page.locator('button.sidesheet-close-button')
    if close_side_sheet.count() and close_side_sheet.first.is_visible():
        close_side_sheet.first.click(force=True)
        page.wait_for_timeout(1000)


def _dismiss_editor_tip(page):
    """關閉首次進入題目編輯器時可能出現的升級提示。"""
    # 「現在試試」會開啟 2/3 步驟教學，遮住填空題的插入空格控制項。
    # 只點關閉或跳過，避免把教學誤當成提示關閉。
    for label in ("知道了", "Got it", "跳過", "Skip"):
        button = page.get_by_role("button", name=label, exact=True)
        try:
            if button.count() and button.first.is_visible():
                button.first.click(force=True)
                page.wait_for_timeout(700)
                return
        except Exception:
            continue


def _fill_question(page, question, index, total):
    """填寫 WayGround 選擇、複選、填空、投票或開放式題目。"""
    q_type = question.get("type", "quiz")
    if q_type not in {"quiz", "multiple_choice", "fill_in_the_blank", "type_answer", "checkbox", "poll", "open_ended"}:
        raise RuntimeError(f"目前尚未實作題型: {q_type}")
    if index > 1:
        quiz_url = page.url.split("?", 1)[0]
        quiz_url = re.sub(r"/question/[^/]+/edit$", "/edit", quiz_url)
        if quiz_url == page.url:
            if not quiz_url.endswith("/edit"):
                raise RuntimeError(f"第 {index} 題無法由目前網址推導測驗編輯器: {page.url}")
        page.goto(quiz_url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2200)
    _close_optional_panels(page)
    if q_type in {"fill_in_the_blank", "type_answer"}:
        type_card = page.locator('[data-testid="question-type-item"]').filter(has_text="填空").first
        side_type_selector = '[data-testid="question-type-indicator-BLANK"]'
    elif q_type == "checkbox":
        type_card = page.locator('[data-testid="question-type-item"]').filter(has_text=re.compile("多項選擇|多選|Multiple Select", re.I)).first
        side_type_selector = '[data-testid="question-type-indicator-MSQ"]'
    elif q_type == "poll":
        type_card = page.locator('[data-testid="question-type-item"]').filter(has_text=re.compile("投票題|Poll", re.I)).first
        side_type_selector = '[data-testid="question-type-indicator-POLL"]'
    elif q_type == "open_ended":
        type_card = page.locator('[data-testid="question-type-item"]').filter(has_text=re.compile("開放式|Open Ended", re.I)).first
        side_type_selector = '[data-testid="question-type-indicator-OPEN"]'
    else:
        type_card = page.locator('[data-testid="question-type-item"]').filter(has_text="選擇題").first
        side_type_selector = '[data-testid="question-type-indicator-MCQ"]'
    editor_ready = page.locator('[data-testid="save-question-button"]')
    direct_editor = editor_ready.count() and editor_ready.first.is_visible()
    if index > 1 and not type_card.count():
        new_question = page.locator('[data-testid="create-new-question-end"]').first
        if not new_question.count():
            new_question = page.locator('[data-testid="create-new-question"]').first
        try:
            new_question.wait_for(state="visible", timeout=15000)
            new_question.click(force=True)
            try:
                editor_ready.wait_for(state="visible", timeout=15000)
                direct_editor = True
            except Exception:
                direct_editor = False
        except Exception:
            raise RuntimeError(f"第 {index} 題找不到新增題目入口。當前網址: {page.url}")
        if not direct_editor:
            # 多題測驗的新增流程會開啟新版側邊選單，使用穩定的題型代碼。
            type_card = page.locator(side_type_selector).first
    if not direct_editor:
        try:
            type_card.wait_for(state="visible", timeout=15000)
        except Exception:
            raise RuntimeError(f"第 {index} 題找不到題型卡片。當前網址: {page.url}")
        type_card.click(force=True)
        page.wait_for_timeout(1200)
        try:
            editor_ready.wait_for(state="visible", timeout=8000)
        except Exception:
            # WayGround 偶爾在側欄動畫尚未結束時吞掉第一次點擊；只在編輯器尚未出現時重試一次。
            _close_optional_panels(page)
            page.locator(side_type_selector).first.click(force=True)
            editor_ready.wait_for(state="visible", timeout=15000)
    _dismiss_editor_tip(page)
    editables = page.locator('[contenteditable="true"]')
    if q_type in {"fill_in_the_blank", "type_answer"}:
        if editables.count() < 1:
            raise RuntimeError(f"第 {index} 題找不到填空題幹編輯區。")
        editor = editables.first
        editor.fill(question["question"])
        editor.press("End")
        add_blank = page.locator('[data-testid="fib-floating-add-blank"]')
        try:
            add_blank.wait_for(state="visible", timeout=15000)
        except Exception:
            raise RuntimeError(f"第 {index} 題找不到新增空白按鈕。")
        add_blank.click(force=True)
        blank_content = page.locator('[data-node-view-content]').first
        blank_content.wait_for(state="visible", timeout=8000)
        blank_content.click(force=True)
        page.keyboard.type(str(question["answers"][0]))
    elif q_type == "open_ended":
        if editables.count() < 1:
            raise RuntimeError(f"第 {index} 題找不到開放式題幹編輯區。")
        editables.first.fill(question["question"])
    else:
        if editables.count() < 1 + len(question.get("answers", [])):
            raise RuntimeError(f"第 {index} 題可編輯欄位不足，實際找到 {editables.count()} 個。")
        editables.nth(0).fill(question["question"])
        for answer_index, answer in enumerate(question["answers"]):
            editables.nth(answer_index + 1).fill(str(answer))
        if q_type == "checkbox":
            for correct in question.get("correct", []):
                marker = page.locator(f'[data-testid="mcq-editor-mark-answer-{correct}-button"]')
                if not marker.count():
                    raise RuntimeError(f"找不到第 {correct} 個複選題正確答案標記按鈕。")
                page.wait_for_timeout(300)
                marker.click(force=True)
        elif q_type in {"quiz", "multiple_choice"}:
            correct = question.get("correct", 0)
            marker = page.locator(f'[data-testid="mcq-editor-mark-answer-{correct}-button"]')
            if not marker.count():
                raise RuntimeError(f"找不到第 {correct} 個正確答案標記按鈕。")
            page.wait_for_timeout(800)
            marker.click(force=True)
    save_button = page.locator('[data-testid="save-question-button"]')
    if not save_button.count():
        raise RuntimeError("找不到儲存題目按鈕。")
    save_button.click(force=True)
    page.wait_for_timeout(1500)
    saved_url = page.url
    if index == 1 and "/question/" in saved_url:
        # 首題首次保存會先導向題目路由；再次保存可讓它正式掛回測驗清單。
        second_save = page.locator('[data-testid="save-question-button"]')
        if second_save.count() and second_save.first.is_visible():
            second_save.click(force=True)
            page.wait_for_timeout(1800)
            saved_url = page.url
    quiz_url = saved_url.split("?", 1)[0]
    quiz_url = re.sub(r"/question/[^/]+/edit$", "/edit", quiz_url)
    if quiz_url.endswith("/edit"):
        page.goto(quiz_url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    card_count = page.locator('[data-testid^="question-details-card-"]').count()
    print(f"    [診斷] 題目儲存後網址: {page.url}；草稿題數: {card_count}")


def _save_debug(page, label="error"):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    shot_path = DEBUG_DIR / f"{label}_screenshot.png"
    dom_path = DEBUG_DIR / f"{label}_dom.html"
    page.screenshot(path=str(shot_path), full_page=True)
    dom_path.write_text(page.content(), encoding="utf-8")
    return shot_path, dom_path

def cmd_create(args):
    """讀取 JSON 題目並在 WayGround/Quizizz 進行自動出題。"""
    content_path = Path(args.content).resolve()
    quiz_data = _validate_quiz_json(content_path)

    print("========================================")
    print(f" 🎮 準備在 WayGround 建立測驗: {quiz_data['title']}")
    print(f" 📋 題目數量: {len(quiz_data['questions'])} 題")
    print("========================================")

    if args.dry_run:
        print("[Dry-Run Mode] 題目格式校驗完全成功！不執行實際瀏覽器出題。")
        return

    if not STATE_FILE.is_file() and not getattr(args, "cdp_url", None):
        die(f"找不到登入憑證 ({STATE_FILE})。請先執行 chrome-login 與 grab-session。")

    sync_playwright = _need_playwright()

    with sync_playwright() as p:
        cdp_url = getattr(args, "cdp_url", None)
        if cdp_url:
            browser = p.chromium.connect_over_cdp(cdp_url.rstrip("/"))
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            owns_browser = False
        else:
            browser = p.chromium.launch(headless=not args.no_headless)
            context = browser.new_context(storage_state=str(STATE_FILE))
            page = context.new_page()
            owns_browser = True

        try:
            print("[資訊] 開啟 WayGround 建立評估流程...")
            _open_new_assessment(page)
            print(f"[資訊] 已進入新測驗編輯器: {page.url}")
            print("[警告] 標題儲存介面尚未驗證成功，本次先保留 WayGround 預設標題。")

            print(f"[資訊] 正在處理 {len(quiz_data['questions'])} 道題目...")
            for idx, q in enumerate(quiz_data["questions"], start=1):
                print(f"  └─ [{idx}/{len(quiz_data['questions'])}] 填寫題目: {q['question'][:20]}...")
                _fill_question(page, q, idx, len(quiz_data["questions"]))

            final_quiz_url = page.url.split("?", 1)[0]
            if final_quiz_url.endswith("/edit"):
                page.goto(final_quiz_url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
            current_text = page.locator("body").inner_text()
            missing_questions = [q for q in quiz_data["questions"] if q["question"] not in current_text]
            if missing_questions:
                print(f"[資訊] 草稿頁缺少 {len(missing_questions)} 題，啟動補題流程...")
                for q in missing_questions:
                    _fill_question(page, q, len(quiz_data["questions"]) + 1, len(quiz_data["questions"]))
                final_quiz_url = page.url.split("?", 1)[0]
                page.goto(final_quiz_url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
            final_text = page.locator("body").inner_text()
            missing_after_repair = [q["question"] for q in quiz_data["questions"] if q["question"] not in final_text]
            if missing_after_repair:
                raise RuntimeError(f"草稿內容驗證失敗，仍缺少題目: {missing_after_repair}")

            if args.publish:
                print("[資訊] 正在發布測驗...")
                publish_btn = page.locator('[data-testid="publish-quiz-button"]')
                if not publish_btn.count():
                    raise RuntimeError("找不到發布按鈕；未宣稱發布成功。")
                publish_btn.click()
                page.wait_for_timeout(3000)
                print(f"[成功] 已送出發布流程。當前網址: {page.url}")
            else:
                print(f"[成功] 題目已儲存；測驗保留在 WayGround 草稿流程。當前網址: {page.url}")

        except Exception as e:
            shot_path, dom_path = _save_debug(page)
            print(f"[錯誤] 出題過程發生例外: {e}")
            print(f"[資訊] 現場快照已存至: {shot_path}")
            print(f"[資訊] 現場 DOM 已存至: {dom_path}")
        finally:
            if cdp_url:
                page.close()
            elif owns_browser:
                browser.close()

def main():
    parser = argparse.ArgumentParser(
        description="WayGround (Quizizz) CLI 自動化出題工具 | 奕鈞老師 (https://ijun-ai.com/)"
    )
    subparsers = parser.add_subparsers(dest="command", help="可用的子指令")

    # 1. doctor
    parser_doctor = subparsers.add_parser("doctor", help="診斷本機環境與登入狀態")
    parser_doctor.add_argument("--chrome-path", help="自訂 Google Chrome 執行檔路徑")

    # 2. chrome-login
    parser_login = subparsers.add_parser("chrome-login", help="開啟專用 Chrome 視窗供手動登入")
    parser_login.add_argument("--port", type=int, default=DEFAULT_CDP_PORT, help="CDP 偵錯埠號 (預設: 9335)")
    parser_login.add_argument("--chrome-path", help="自訂 Google Chrome 執行檔路徑")

    # 3. grab-session
    parser_grab = subparsers.add_parser("grab-session", help="從專用 Chrome 擷取 SessionCookies 並寫入 state.json")
    parser_grab.add_argument("--cdp-url", help="CDP 連線 URL (例如: http://127.0.0.1:9335)")

    # 4. check
    parser_check = subparsers.add_parser("check", help="驗證 state.json 登入憑證是否有效")
    parser_check.add_argument("--no-headless", action="store_true", help="以有頭模式顯示瀏覽器視窗")

    # 5. create
    parser_create = subparsers.add_parser("create", help="讀取 JSON 題目並自動建立測驗")
    parser_create.add_argument("--content", required=True, help="題目 JSON 檔案路徑")
    parser_create.add_argument("--publish", action="store_true", help="建立完畢直接發布 (預設存為草稿)")
    parser_create.add_argument("--premium", action="store_true", help="使用付費 / 進階題型")
    parser_create.add_argument("--no-headless", action="store_true", help="以有頭模式顯示瀏覽器視窗")
    parser_create.add_argument("--cdp-url", help="使用已登入 Chrome 的 CDP 連線（例如: http://127.0.0.1:9335）")
    parser_create.add_argument("--dry-run", action="store_true", help="僅校驗 JSON 格式，不啟動瀏覽器")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "doctor": cmd_doctor,
        "chrome-login": cmd_chrome_login,
        "grab-session": cmd_grab_session,
        "check": cmd_check,
        "create": cmd_create,
    }

    cmd_func = dispatch.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
