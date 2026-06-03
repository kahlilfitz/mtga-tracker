# MTG Arena Tracker

A local dashboard for tracking your MTG Arena match history, rank, and inventory. No accounts, no cloud — reads directly from the game's log file on macOS.

<img width="1333" height="697" alt="Screenshot 2026-06-02 at 5 16 02 PM" src="https://github.com/user-attachments/assets/903b0dd9-7382-4dbd-88c6-acdb7b50d004" />

## Features

- Win/loss record per session and overall
- Constructed and limited rank
- Inventory: gold, gems, wildcards
- Recent match table with opponent names and formats
- Auto-updates when you close MTG Arena

## Requirements

- macOS
- MTG Arena (Mac client)
- Python 3 (pre-installed on macOS)

## Setup

### 1. Enable Detailed Logs in MTG Arena

In-game: **Settings → Account → Detailed Logs (Verbose) → ON**

Restart the game after enabling. This is required — without it, the log contains no match data.

### 2. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/mtga-tracker.git
cd mtga-tracker
```

### 3. Run the parser

```bash
python3 parse.py
```

This reads `Player.log` and writes `data.json` in the same folder.

### 4. Open the dashboard

Open `index.html` in your browser, or serve it locally:

```bash
python3 -m http.server 7890
# then open http://localhost:7890
```

Hit **↻ Refresh** in the dashboard after running the parser to see updated data.

## Auto-update on game close (macOS)

A launchd agent watches the log file and runs `parse.py` automatically when MTG Arena closes.

1. Copy the template plist and fill in your paths:

```bash
cp launchd/com.mtga-tracker.plist.template ~/Library/LaunchAgents/com.mtga-tracker.plist
```

2. Edit `~/Library/LaunchAgents/com.mtga-tracker.plist` — replace all `REPLACE_WITH_*` placeholders with your actual paths and username.

3. Load the agent:

```bash
launchctl load ~/Library/LaunchAgents/com.mtga-tracker.plist
```

To unload: `launchctl unload ~/Library/LaunchAgents/com.mtga-tracker.plist`

## How it works

MTG Arena writes structured JSON events to:
```
~/Library/Logs/Wizards Of The Coast/MTGA/Player.log
```

The parser reads both `Player.log` and `Player-prev.log` (the previous session) to extract match results, rank, and inventory. No network requests are made.

## Privacy

All data stays local. Nothing is sent anywhere.
