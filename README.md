# MTG Arena Tracker

A local desktop dashboard for tracking your MTG Arena match history, rank, and inventory. No accounts, no cloud — reads directly from the game's log file.

<img width="1333" height="697" alt="Screenshot 2026-06-02 at 5 16 02 PM" src="https://github.com/user-attachments/assets/903b0dd9-7382-4dbd-88c6-acdb7b50d004" />

## Features

- Win/loss record per session and overall
- Constructed and limited rank
- Inventory: gold, gems, wildcards
- Recent match table with opponent names and formats
- Per-draft color distribution and mana curve
- Manual refresh — re-parses the log on demand

## Install

Download the latest release for your platform from the [Releases page](https://github.com/kahlilfitz/mtga-tracker/releases/latest):

### macOS

1. Download `MTG-Arena-Tracker-<version>-arm64.dmg`
2. Open the `.dmg` and drag **MTG Arena Tracker** into Applications
3. On first launch, since the app isn't notarized: right-click the app → **Open** → **Open** (or go to **System Settings → Privacy & Security** and click **Open Anyway**)

### Windows

1. Download `MTG-Arena-Tracker-Setup-<version>.exe`
2. Run the installer
3. If SmartScreen blocks it (the app isn't code-signed), click **More info → Run anyway**

A portable `.zip` build is also available for both platforms if you'd rather not install.

## Setup (after install)

### Enable Detailed Logs in MTG Arena

In-game: **Settings → Account → Detailed Logs (Verbose) → ON**

Restart MTG Arena after enabling. This is required — without it, the log contains no match data.

### Run the app

Launch **MTG Arena Tracker** and click **↻ Refresh** to parse the latest log data. Click Refresh again any time after playing matches to update the dashboard.

## Development

### Requirements

- Node.js 18+
- macOS or Windows (matching MTG Arena's log paths)

### Setup

```bash
git clone https://github.com/kahlilfitz/mtga-tracker.git
cd mtga-tracker
npm install
npm start
```

### Building release artifacts

```bash
npm run dist
```

Produces platform-specific installers/archives in `dist/` via electron-builder (`.dmg`/`.zip` on macOS, `.exe`/`.zip` on Windows). Cross-building Windows artifacts from macOS isn't supported — use the CI workflow below or build natively on each platform.

### Cutting a versioned release

1. Bump `"version"` in `package.json` (semver).
2. Commit and tag: `git tag v1.1.0 && git push origin v1.1.0`
3. The [release workflow](.github/workflows/release.yml) builds macOS and Windows artifacts and publishes them to a new GitHub Release automatically.

## How it works

MTG Arena writes structured JSON events to a local log file:

- macOS: `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`
- Windows: `%USERPROFILE%\AppData\LocalLow\Wizards Of The Coast\MTGA\Player.log`

When you click Refresh, the app's main process reads both `Player.log` and `Player-prev.log` (the previous session) to extract match results, rank, and inventory, plus queries MTG Arena's local card database for deck color/mana curve stats. No network requests are made.

## Privacy

All data stays local. Nothing is sent anywhere.
