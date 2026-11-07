# Wayground Upload & Assign Workflow Guide

This guide references the screenshots located in the `screenshots/` folder (`Step_00.png` through `Step_25.png`). The agent must use these specific filenames to map the visual steps and build robust Playwright selectors. 

## ⚠️ Critical Handling Rules (Read First)
1. **The Premium Popup (`Step_00.png`):** Because Playwright launches a fresh browser session, Wayground will almost always show the "Unlock 45+ premium features" popup on the first page load. The agent MUST detect this modal and click the `X` in the top right to dismiss it before clicking anything else.
2. **The Accommodations Redirect (`Step_16.png`):** If the full-page "Set up learning supports" screen appears, it means Wayground is prompting for accommodations (usually on the first time clicking "Select a class"). The agent MUST click the `< Back` (left arrow) button in the top left corner to return to the assignment modal.
3. **Default Checked Box:** In the "Assign to a class" modal, the "Set up Accommodations" checkbox is CHECKED BY DEFAULT. The agent MUST explicitly uncheck it.

---

## Phase 1: Create & Publish

- **`Step_00.png` (Premium Popup):** Dismiss the premium modal by clicking the `X` top right.
- **`Step_01.png` (Start):** On the "How would you like to get started?" page, click the **"Start from scratch"** tile (bottom right).
    - *Wait:* `page.wait_for_url("**/activity/admin/quiz/*/edit?fromCreateFlow=true")`
- **`Step_02.png` (Empty Editor):** Scroll down to "Import existing files" and click the **"Spreadsheet"** button.
- **`Step_03.png` (Import Modal):** Inject the `.xlsx` file using Playwright's `setInputFiles()`.
- **`Step_04.png` (Error State):** If the red **"Invalid correct option"** banner appears, log the row numbers, click "Cancel", and abort this file (unless `--force-import` is set).
- **`Step_05.png` (Success State):** Wait for the green **"Looks good!"** banner. Click the purple **"Import"** button in the bottom right.
- **`Step_06.png` (Editor with Questions):** Quiz questions are loaded. Click the pink **"Publish"** button in the top right.
- **`Step_07.png` (Publish Modal Default):** The "Great, you're almost done" modal appears. It is blank/default, no errors yet.
- **`Step_08.png` (Subject Dropdown):** Click the "Subject" dropdown and select **"Information Technology (IT)"**.
- **`Step_09.png` (Grade Dropdown):** Click the "Grade" dropdown and select **"University"**.
- **`Step_10.png` (Name Input):** Type the quiz name into the "Name" input field.
- **`Step_11.png` (Validation Errors):** If any fields (Name, Subject, Grade) were missed, red error text appears. The agent must fill them.
- **`Step_12.png` (Publish Modal Filled):** All fields are filled in, no errors. Click the pink **"Publish"** button inside the modal.
    - *Wait:* `page.wait_for_url("**/activity/admin/quiz/*")` (The `/edit` disappears from the URL).
- **`Step_13.png` (Success Modal):** The "Great work! Your quiz is saved..." modal appears. Click the `X` in the top right to close it.

## Phase 2: Assign to Class

- **`Step_14.png` (Quiz Page):** The URL no longer has `/edit`. You are on the quiz page showing Preview, Assign, and Start now. Click the pink **"Assign"** button in the top right.
    - *Wait:* `page.wait_for_url("**/activity/admin/quiz/homework/*")`
- **`Step_15.png` (Assignment Details Default):** You are now on the Assignment Details page. Click the **"Select a class"** button.
- **`Step_16.png` (Accommodations Redirect):** The full-page "Set up learning supports" appears. Click the **left arrow (`<`)** in the top left to go back to the assignment modal.
- **`Step_17.png` (Class Modal):** The "Assign to a class" modal opens. Click the checkbox for **"Naic"** (or "Select all").
- **`Step_18.png` (Class Selected):** The "Naic" class is now checked. 
- **`Step_19.png` (Uncheck Accommodations):** Uncheck the "Set up Accommodations" box (it is checked by default). Click the pink **"Next"** button.
- **`Step_20.png` (Name Modal):** The "Name this assignment (Optional)" modal appears. Type the quiz name into the top input field. Click the pink **"Next"** button.
- **`Step_21.png` (Assigned Details Page):** Back on the Assignment Details page. The class "Naic" is now assigned. Click the **"Change"** dropdown next to "Start - End time".
- **`Step_22.png` (Time Modal):** The Start - End time modal opens. Click the **"No deadline"** toggle to turn it ON.
- **`Step_23.png` (Save Time):** With "No deadline" toggled ON, click the pink **"Save and close"** button.
- **`Step_24.png` (No Deadline Set):** Back on the Assignment Details page. Ensure the time now shows "No deadline". Click the large pink **"Assign"** button in the top right.
    - *Wait:* `page.wait_for_url("**/gameplay/admin/activity/classic/*")`
- **`Step_25.png` (Gameplay Dashboard):** The gameplay dashboard loads (`joinmyquiz.com`). Click the pink **"Copy Join link"** button to extract the final link.