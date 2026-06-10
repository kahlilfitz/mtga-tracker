# MTG Arena Tracker

A local desktop dashboard for tracking your MTG Arena match history, rank, and inventory. No accounts, no cloud — reads directly from the game's log file on macOS.

<img width="1333" height="697" alt="Screenshot 2026-06-02 at 5 16 02 PM" src="https://github.com/user-attachments/assets/903b0dd9-7382-4dbd-88c6-acdb7b50d004" />

## Features

- Win/loss record per session and overall
- Constructed and limited rank
- Inventory: gold, gems, wildcards
- Recent match table with opponent names and formats
- Per-draft color distribution and mana curve
- Manual refresh — re-parses the log on demand

## Requirements

- macOS
- MTG Arena (Mac client)
- Node.js 18+ (for development/building)

## Setup

### 1. Enable Detailed Logs in MTG Arena

In-game: **Settings → Account → Detailed Logs (Verbose) → ON**

Restart the game after enabling. This is required — without it, the log contains no match data.

### 2. Clone the repo and install dependencies

```bash
git clone https://github.com/YOUR_USERNAME/mtga-tracker.git
cd mtga-tracker
npm install
```

### 3. Run the app

```bash
npm start
```

This launches the Electron app. Click **↻ Refresh** to re-parse the latest log data.

### 4. Build a standalone app (optional)

```bash
npm run dist
```

Produces a packaged `.app` in `dist/` via electron-builder.

## How it works

MTG Arena writes structured JSON events to:
```
~/Library/Logs/Wizards Of The Coast/MTGA/Player.log
```

When you click Refresh, the app's main process reads both `Player.log` and `Player-prev.log` (the previous session) to extract match results, rank, and inventory, plus queries MTG Arena's local card database for deck color/mana curve stats. No network requests are made.

## Privacy

All data stays local. Nothing is sent anywhere.
