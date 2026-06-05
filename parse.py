#!/usr/bin/env python3
"""Parse MTG Arena Player.log and write data.json for the dashboard."""

import json
import re
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / "Library/Logs/Wizards Of The Coast/MTGA/Player.log"
PREV_LOG_PATH = Path.home() / "Library/Logs/Wizards Of The Coast/MTGA/Player-prev.log"

# Matches: [UnityCrossThreadLogger]<timestamp>: Match to <playerId>: <EventName>
MATCH_EVENT_RE = re.compile(
    r"\[UnityCrossThreadLogger\].*?: Match to (\S+): (\w+)"
)
# Matches: [UnityCrossThreadLogger]==> <EventName> {json}
REQUEST_RE = re.compile(r"\[UnityCrossThreadLogger\]==>\s+(\w+)\s+(\{.*)")


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    with open(path, "r", errors="replace") as f:
        return f.readlines()


def parse_log(lines: list[str]) -> dict:
    my_player_id: str | None = None
    matches: list[dict] = []
    rank: dict | None = None
    inventory: dict | None = None

    # Track active match context (Playing state)
    active_match: dict = {}
    pending_deck_name: str = ""  # set by EventSetDeckV3 right before each match

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # --- Match events (server → client) ---
        m = MATCH_EVENT_RE.match(line)
        if m:
            target_player = m.group(1)
            event_name = m.group(2)
            # Next non-empty line should be the JSON payload
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("{"):
                j += 1
            payload = None
            if j < len(lines):
                try:
                    payload = json.loads(lines[j].strip())
                except json.JSONDecodeError:
                    pass

            if payload and event_name == "MatchGameRoomStateChangedEvent":
                gri = payload.get("matchGameRoomStateChangedEvent", {}).get("gameRoomInfo", {})
                config = gri.get("gameRoomConfig", {})
                state_type = gri.get("stateType", "")
                players = config.get("reservedPlayers", [])
                match_id = config.get("matchId", "")
                event_id = ""

                # Detect my player ID: the target in the log line IS the local player
                # Store it the first time we see a match event
                if not my_player_id and target_player:
                    my_player_id = target_player

                if state_type == "MatchGameRoomStateType_Playing":
                    my_team = None
                    opponent_name = ""
                    for p in players:
                        if p.get("userId") == my_player_id:
                            my_team = p.get("teamId")
                            event_id = p.get("eventId", "")
                        else:
                            opponent_name = p.get("playerName", "")
                    active_match = {
                        "matchId": match_id,
                        "format": event_id,
                        "deckName": pending_deck_name,
                        "opponentName": opponent_name,
                        "myTeamId": my_team,
                        "timestamp": payload.get("timestamp", ""),
                    }
                    pending_deck_name = ""

                elif state_type == "MatchGameRoomStateType_MatchCompleted":
                    result_list = (
                        gri.get("finalMatchResult", {}).get("resultList", [])
                    )
                    result = "unknown"
                    for r in result_list:
                        if r.get("scope") == "MatchScope_Match":
                            winning_team = r.get("winningTeamId")
                            result = "win" if winning_team == active_match.get("myTeamId") else "loss"
                            break
                    # Fallback: use last game result
                    if result == "unknown":
                        for r in result_list:
                            if r.get("scope") == "MatchScope_Game":
                                winning_team = r.get("winningTeamId")
                                result = "win" if winning_team == active_match.get("myTeamId") else "loss"
                                break

                    # Merge opponent info if active_match doesn't have it yet
                    if not active_match.get("opponentName"):
                        for p in players:
                            if p.get("userId") != my_player_id:
                                active_match["opponentName"] = p.get("playerName", "")

                    record = {**active_match, "matchId": match_id or active_match.get("matchId", ""), "result": result}
                    matches.append(record)
                    active_match = {}

            i = j + 1
            continue

        # --- API responses (bare JSON lines after ==> requests) ---
        # Detect ==> requests to know what the next JSON block is
        req_m = REQUEST_RE.match(line)
        if req_m:
            event_name = req_m.group(1)
            # Capture deck name submitted right before each match
            if event_name == "EventSetDeckV3":
                try:
                    outer = json.loads(req_m.group(2))
                    req = json.loads(outer.get("request", "{}"))
                    summary = req.get("Summary", {})
                    pending_deck_name = summary.get("Name", "")
                except (json.JSONDecodeError, KeyError):
                    pass
            # No per-request response scanning needed — rank/inventory parsed via direct scan below
            i += 1
            continue

        # Also catch bare InventoryInfo lines not attached to a ==> request
        if line.strip().startswith('{"InventoryInfo"'):
            try:
                payload = json.loads(line.strip())
                inv_info = payload.get("InventoryInfo")
                if inv_info:
                    inventory = {
                        "gold": inv_info.get("Gold", 0),
                        "gems": inv_info.get("Gems", 0),
                        "wcCommon": inv_info.get("WildCardCommons", 0),
                        "wcUncommon": inv_info.get("WildCardUnCommons", 0),
                        "wcRare": inv_info.get("WildCardRares", 0),
                        "wcMythic": inv_info.get("WildCardMythics", 0),
                    }
            except json.JSONDecodeError:
                pass

        i += 1

    # Direct scan for rank and inventory — responses arrive async, not right after requests
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        if "constructedClass" in payload:
            rank = {
                "constructed": {
                    "class": payload.get("constructedClass", ""),
                    "level": payload.get("constructedLevel", 0),
                    "step": payload.get("constructedStep", 0),
                    "wins": payload.get("constructedMatchesWon", 0),
                    "losses": payload.get("constructedMatchesLost", 0),
                },
                "limited": {
                    "class": payload.get("limitedClass", "Bronze"),
                    "level": payload.get("limitedLevel", 1),
                    "step": payload.get("limitedStep", 0),
                    "wins": payload.get("limitedMatchesWon", 0),
                    "losses": payload.get("limitedMatchesLost", 0),
                },
            }

        inv_info = payload.get("InventoryInfo")
        if inv_info:
            inventory = {
                "gold": inv_info.get("Gold", 0),
                "gems": inv_info.get("Gems", 0),
                "wcCommon": inv_info.get("WildCardCommons", 0),
                "wcUncommon": inv_info.get("WildCardUnCommons", 0),
                "wcRare": inv_info.get("WildCardRares", 0),
                "wcMythic": inv_info.get("WildCardMythics", 0),
            }

    return {
        "myPlayerId": my_player_id,
        "matches": matches,
        "rank": rank,
        "inventory": inventory,
    }


def build_data() -> dict:
    lines = read_lines(PREV_LOG_PATH) + read_lines(LOG_PATH)
    parsed = parse_log(lines)

    matches = parsed["matches"]
    wins = sum(1 for m in matches if m["result"] == "win")
    losses = sum(1 for m in matches if m["result"] == "loss")

    return {
        "generated": datetime.now().isoformat(),
        "detailedLogsEnabled": bool(matches or parsed["rank"] or parsed["inventory"]),
        "summary": {
            "wins": wins,
            "losses": losses,
            "total": len(matches),
            "winRate": round(wins / len(matches) * 100, 1) if matches else 0,
        },
        "rank": parsed["rank"],
        "inventory": parsed["inventory"],
        "matches": matches[-100:],
        "drafts": [],
    }


if __name__ == "__main__":
    data = build_data()
    out = Path(__file__).parent / "data.json"
    out.write_text(json.dumps(data, indent=2))
    r = data["rank"]
    rank_str = f"{r['constructed']['class']} {r['constructed']['level']}" if r else "unknown"
    print(f"Parsed {data['summary']['total']} matches | Rank: {rank_str} | W:{data['summary']['wins']} L:{data['summary']['losses']}")
    if data["inventory"]:
        inv = data["inventory"]
        print(f"Inventory: {inv['gold']}g {inv['gems']} gems | WC: {inv['wcCommon']}C {inv['wcUncommon']}U {inv['wcRare']}R {inv['wcMythic']}M")
