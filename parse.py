#!/usr/bin/env python3
"""Parse MTG Arena Player.log and write data.json for the dashboard."""

import json
import re
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / "Library/Logs/Wizards Of The Coast/MTGA/Player.log"
PREV_LOG_PATH = Path.home() / "Library/Logs/Wizards Of The Coast/MTGA/Player-prev.log"

# Draft/limited format prefixes
DRAFT_PREFIXES = ("Draft", "Sealed", "Limited", "PremierDraft", "QuickDraft",
                  "TradDraft", "ArenaLimited", "BotDraft")

# Matches: [UnityCrossThreadLogger]<timestamp>: Match to <playerId>: <EventName>
MATCH_EVENT_RE = re.compile(
    r"\[UnityCrossThreadLogger\].*?: Match to (\S+): (\w+)"
)
# Matches: [UnityCrossThreadLogger]==> <EventName> {json}
REQUEST_RE = re.compile(r"\[UnityCrossThreadLogger\]==>\s+(\w+)\s+(\{.*)")


def is_draft_format(fmt: str) -> bool:
    return any(fmt.startswith(p) for p in DRAFT_PREFIXES)


def format_display(fmt: str) -> str:
    """Convert internal event name to a human-readable label."""
    if not fmt:
        return "Unknown"
    # PremierDraft_SOS_20260421 → "Premier Draft · SOS"
    # ArenaLimitedQualifier_Draft1_20260605 → "Arena Limited Qualifier"
    # Ladder → "Ladder"
    parts = fmt.split("_")
    # Strip trailing date-like segment (8 digits)
    if parts and re.fullmatch(r"\d{8}", parts[-1]):
        parts = parts[:-1]
    # Insert spaces before capital letters in the first segment
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", parts[0])
    if len(parts) > 1 and not re.fullmatch(r"\d+", parts[1]):
        label += f" · {parts[1]}"
    return label


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

    active_match: dict = {}
    pending_deck_name: str = ""
    # Persist deck name per event so all games in a draft event show it
    deck_by_event: dict[str, str] = {}

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # --- Match events (server → client) ---
        m = MATCH_EVENT_RE.match(line)
        if m:
            target_player = m.group(1)
            event_name = m.group(2)
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

                    # Resolve deck name: pending > stored per-event > empty
                    deck_name = pending_deck_name
                    if not deck_name and event_id:
                        deck_name = deck_by_event.get(event_id, "")
                    # Store it so future games in this event inherit it
                    if deck_name and event_id:
                        deck_by_event[event_id] = deck_name

                    active_match = {
                        "matchId": match_id,
                        "format": event_id,
                        "deckName": deck_name,
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
                    if result == "unknown":
                        for r in result_list:
                            if r.get("scope") == "MatchScope_Game":
                                winning_team = r.get("winningTeamId")
                                result = "win" if winning_team == active_match.get("myTeamId") else "loss"
                                break

                    if not active_match.get("opponentName"):
                        for p in players:
                            if p.get("userId") != my_player_id:
                                active_match["opponentName"] = p.get("playerName", "")

                    record = {**active_match, "matchId": match_id or active_match.get("matchId", ""), "result": result}
                    matches.append(record)
                    active_match = {}

            i = j + 1
            continue

        # --- Outgoing requests ---
        req_m = REQUEST_RE.match(line)
        if req_m:
            event_name = req_m.group(1)
            if event_name == "EventSetDeckV3":
                try:
                    outer = json.loads(req_m.group(2))
                    req = json.loads(outer.get("request", "{}"))
                    summary = req.get("Summary", {})
                    name = summary.get("Name", "")
                    mtga_event = req.get("EventName", "")
                    if name:
                        pending_deck_name = name
                        if mtga_event:
                            deck_by_event[mtga_event] = name
                except (json.JSONDecodeError, KeyError):
                    pass
            i += 1
            continue

        # Bare InventoryInfo lines
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

    # Direct scan for rank and inventory
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


def build_drafts(matches: list[dict]) -> list[dict]:
    """Group draft/limited matches by event into draft summaries."""
    events: dict[str, dict] = {}
    for m in matches:
        fmt = m.get("format", "")
        if not is_draft_format(fmt):
            continue
        if fmt not in events:
            events[fmt] = {
                "eventId": fmt,
                "displayName": format_display(fmt),
                "deckName": "",
                "wins": 0,
                "losses": 0,
                "matches": [],
            }
        e = events[fmt]
        if m.get("deckName") and not e["deckName"]:
            e["deckName"] = m["deckName"]
        if m["result"] == "win":
            e["wins"] += 1
        elif m["result"] == "loss":
            e["losses"] += 1
        e["matches"].append(m)

    # Sort by first match timestamp descending
    def first_ts(e):
        ts = e["matches"][0].get("timestamp", "")
        return ts

    return sorted(events.values(), key=first_ts, reverse=True)


def build_data() -> dict:
    lines = read_lines(PREV_LOG_PATH) + read_lines(LOG_PATH)
    parsed = parse_log(lines)

    matches = parsed["matches"]
    wins = sum(1 for m in matches if m["result"] == "win")
    losses = sum(1 for m in matches if m["result"] == "loss")
    drafts = build_drafts(matches)

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
        "drafts": drafts,
    }


if __name__ == "__main__":
    data = build_data()
    out = Path(__file__).parent / "data.json"
    out.write_text(json.dumps(data, indent=2))
    r = data["rank"]
    rank_str = f"{r['constructed']['class']} {r['constructed']['level']}" if r else "unknown"
    print(f"Parsed {data['summary']['total']} matches | Rank: {rank_str} | W:{data['summary']['wins']} L:{data['summary']['losses']}")
    if data["drafts"]:
        print(f"Drafts: {len(data['drafts'])} events")
        for d in data["drafts"]:
            print(f"  {d['displayName']:35} {d['deckName'] or '(no deck name)':25} {d['wins']}W-{d['losses']}L")
    if data["inventory"]:
        inv = data["inventory"]
        print(f"Inventory: {inv['gold']}g {inv['gems']} gems | WC: {inv['wcCommon']}C {inv['wcUncommon']}U {inv['wcRare']}R {inv['wcMythic']}M")
