#!/usr/bin/env python3
"""Parse MTG Arena Player.log and write data.json for the dashboard."""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / "Library/Logs/Wizards Of The Coast/MTGA/Player.log"
PREV_LOG_PATH = Path.home() / "Library/Logs/Wizards Of The Coast/MTGA/Player-prev.log"
CARD_DB_DIR = Path.home() / "Library/Application Support/com.wizards.mtga/Downloads/Raw"

DRAFT_PREFIXES = ("Draft", "Sealed", "Limited", "PremierDraft", "QuickDraft",
                  "TradDraft", "ArenaLimited", "BotDraft")

MATCH_EVENT_RE = re.compile(r"\[UnityCrossThreadLogger\].*?: Match to (\S+): (\w+)")
REQUEST_RE = re.compile(r"\[UnityCrossThreadLogger\]==>\s+(\w+)\s+(\{.*)")

COLOR_MAP = {"1": "W", "2": "U", "3": "B", "4": "R", "5": "G"}


def is_draft_format(fmt: str) -> bool:
    return any(fmt.startswith(p) for p in DRAFT_PREFIXES)


def format_display(fmt: str) -> str:
    if not fmt:
        return "Unknown"
    parts = fmt.split("_")
    if parts and re.fullmatch(r"\d{8}", parts[-1]):
        parts = parts[:-1]
    label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", parts[0])
    if len(parts) > 1 and not re.fullmatch(r"\d+", parts[1]):
        label += f" · {parts[1]}"
    return label


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    with open(path, "r", errors="replace") as f:
        return f.readlines()


def find_card_db() -> Path | None:
    if not CARD_DB_DIR.exists():
        return None
    for f in CARD_DB_DIR.glob("Raw_CardDatabase_*.mtga"):
        return f
    return None


RARITY_MAP = {1: "land", 2: "common", 3: "uncommon", 4: "rare", 5: "mythic"}


def load_card_data(card_ids: set[int]) -> dict[int, dict]:
    """Query local MTGA card DB for colors, CMC, rarity and name."""
    db_path = find_card_db()
    if not db_path or not card_ids:
        return {}
    result = {}
    try:
        con = sqlite3.connect(str(db_path))
        ids_str = ",".join(str(i) for i in card_ids)
        rows = con.execute(
            f"SELECT c.GrpId, c.Colors, c.Order_CMCWithXLast, c.IsToken, c.Rarity, l.Loc "
            f"FROM Cards c "
            f"LEFT JOIN Localizations_enUS l ON l.LocId=c.TitleId AND l.Formatted=1 "
            f"WHERE c.GrpId IN ({ids_str})"
        ).fetchall()
        con.close()
        for grp_id, colors_raw, cmc, is_token, rarity, name in rows:
            color_vals = [c.strip() for c in (colors_raw or "").split(",") if c.strip()]
            colors = [COLOR_MAP[c] for c in color_vals if c in COLOR_MAP]
            result[grp_id] = {
                "colors": colors,
                "cmc": cmc or 0,
                "isToken": bool(is_token),
                "rarity": RARITY_MAP.get(rarity, "common"),
                "name": re.sub(r"<[^>]+>", "", name or "") or f"Card #{grp_id}",
            }
    except Exception:
        pass
    return result


def deck_stats(deck: dict[int, int]) -> dict:
    """Given {cardId: quantity}, return color distribution and mana curve with rarity/names."""
    all_ids = set(deck.keys())
    card_data = load_card_data(all_ids)

    color_counts: dict[str, int] = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0}
    # curve_detail: cmc_bucket → {rarity → [{name, qty}]}
    curve_detail: dict[int, dict[str, list]] = {}

    for card_id, qty in deck.items():
        info = card_data.get(card_id)
        if not info or info["isToken"]:
            continue
        for color in info["colors"]:
            if color in color_counts:
                color_counts[color] += qty
        cmc = info["cmc"]
        rarity = info["rarity"]
        # Skip basic lands (no colors, cmc=0, rarity=land)
        if rarity == "land" and cmc == 0 and not info["colors"]:
            continue
        bucket = min(cmc, 7)
        if bucket not in curve_detail:
            curve_detail[bucket] = {"mythic": [], "rare": [], "uncommon": [], "common": [], "land": []}
        curve_detail[bucket][rarity].append({"name": info["name"], "qty": qty})

    # Remove zero-count colors
    color_counts = {k: v for k, v in color_counts.items() if v > 0}

    # Build compact curve for JSON: bucket → {total, rarities: {mythic,rare,uncommon,common}, cards: [...]}
    curve_out = {}
    for bucket, rarities in sorted(curve_detail.items()):
        total = sum(sum(c["qty"] for c in cards) for cards in rarities.values())
        rarity_totals = {r: sum(c["qty"] for c in cards) for r, cards in rarities.items() if cards}
        all_cards = []
        for r in ["mythic", "rare", "uncommon", "common", "land"]:
            for c in rarities.get(r, []):
                all_cards.append({"name": c["name"], "qty": c["qty"], "rarity": r})
        curve_out[str(bucket)] = {"total": total, "rarities": rarity_totals, "cards": all_cards}

    return {"colors": color_counts, "curve": curve_out}


def parse_log(lines: list[str]) -> dict:
    my_player_id: str | None = None
    matches: list[dict] = []
    rank: dict | None = None
    inventory: dict | None = None

    active_match: dict = {}
    pending_deck_name: str = ""
    pending_deck_id: str = ""
    pending_deck_list: dict[int, int] = {}  # cardId → qty from EventSetDeckV3
    deck_by_event: dict[str, str] = {}
    deck_id_by_event: dict[str, str] = {}
    deck_list_by_event: dict[str, dict[int, int]] = {}
    # Per deck_id (individual run) card lists
    deck_list_by_id: dict[str, dict[int, int]] = {}

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

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

                    deck_name = pending_deck_name or deck_by_event.get(event_id, "")
                    deck_id = pending_deck_id or deck_id_by_event.get(event_id, "")
                    deck_list = pending_deck_list or deck_list_by_event.get(event_id, {})
                    if deck_name and event_id:
                        deck_by_event[event_id] = deck_name
                    if deck_id and event_id:
                        deck_id_by_event[event_id] = deck_id
                    if deck_list and event_id:
                        deck_list_by_event[event_id] = deck_list
                    if deck_id and deck_list:
                        deck_list_by_id[deck_id] = deck_list

                    active_match = {
                        "matchId": match_id,
                        "format": event_id,
                        "deckName": deck_name,
                        "deckId": deck_id,
                        "opponentName": opponent_name,
                        "myTeamId": my_team,
                        "timestamp": payload.get("timestamp", ""),
                    }
                    pending_deck_name = ""
                    pending_deck_id = ""
                    pending_deck_list = {}

                elif state_type == "MatchGameRoomStateType_MatchCompleted":
                    result_list = gri.get("finalMatchResult", {}).get("resultList", [])
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

        req_m = REQUEST_RE.match(line)
        if req_m:
            en = req_m.group(1)
            if en == "EventSetDeckV3":
                try:
                    outer = json.loads(req_m.group(2))
                    req = json.loads(outer.get("request", "{}"))
                    summary = req.get("Summary", {})
                    name = summary.get("Name", "")
                    mtga_event = req.get("EventName", "")
                    raw_deck = req.get("Deck", {})
                    main_deck = raw_deck.get("MainDeck", [])
                    deck_list = {entry["cardId"]: entry["quantity"]
                                 for entry in main_deck if "cardId" in entry}
                    d_id = summary.get("DeckId", "")
                    if name:
                        pending_deck_name = name
                        if mtga_event:
                            deck_by_event[mtga_event] = name
                    if d_id:
                        pending_deck_id = d_id
                        if mtga_event:
                            deck_id_by_event[mtga_event] = d_id
                    if deck_list:
                        pending_deck_list = deck_list
                        if mtga_event:
                            deck_list_by_event[mtga_event] = deck_list
                        if d_id:
                            deck_list_by_id[d_id] = deck_list
                except (json.JSONDecodeError, KeyError):
                    pass
            i += 1
            continue

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
        "deck_list_by_event": deck_list_by_event,
        "deck_list_by_id": deck_list_by_id,
    }


def longestStreak(matches):
    best = {"win": 0, "loss": 0}
    cur = {"win": 0, "loss": 0}
    for m in matches:
        if m["result"] == "win":
            cur["win"] += 1; cur["loss"] = 0
        else:
            cur["loss"] += 1; cur["win"] = 0
        best["win"] = max(best["win"], cur["win"])
        best["loss"] = max(best["loss"], cur["loss"])
    return best


def build_drafts(matches: list[dict], deck_list_by_event: dict, deck_list_by_id: dict) -> list[dict]:
    """Build one record per individual draft run (unique deckId within a format)."""
    # Key: (format, deckId) — deckId="" for runs where we couldn't identify it
    runs: dict[tuple, dict] = {}
    run_order: list[tuple] = []  # preserve insertion order

    for m in matches:
        fmt = m.get("format", "")
        if not is_draft_format(fmt):
            continue
        deck_id = m.get("deckId", "")
        key = (fmt, deck_id)
        if key not in runs:
            runs[key] = {
                "eventId": fmt,
                "deckId": deck_id,
                "displayName": format_display(fmt),
                "deckName": m.get("deckName", ""),
                "wins": 0,
                "losses": 0,
                "matches": [],
            }
            run_order.append(key)
        r = runs[key]
        if m.get("deckName") and not r["deckName"]:
            r["deckName"] = m["deckName"]
        if m["result"] == "win":
            r["wins"] += 1
        elif m["result"] == "loss":
            r["losses"] += 1
        r["matches"].append(m)

    # Add card stats and streaks per run
    for key, run in runs.items():
        fmt, deck_id = key
        deck_list = deck_list_by_id.get(deck_id) or deck_list_by_event.get(fmt, {})
        run["cardStats"] = deck_stats(deck_list) if deck_list else None
        streak = longestStreak(run["matches"])
        run["bestWinStreak"] = streak["win"]
        run["worstLossStreak"] = streak["loss"]

    # Sort newest first by first match timestamp
    sorted_runs = sorted(
        runs.values(),
        key=lambda r: r["matches"][0].get("timestamp", ""),
        reverse=True,
    )

    # Number runs within each event (Run 1 = most recent)
    event_counters: dict[str, int] = {}
    for run in sorted_runs:
        fmt = run["eventId"]
        event_counters[fmt] = event_counters.get(fmt, 0) + 1

    # Assign numbers oldest→newest so Run 1 is the first ever draft of that event
    event_run_totals = dict(event_counters)
    event_assign: dict[str, int] = {}
    for run in reversed(sorted_runs):
        fmt = run["eventId"]
        event_assign[fmt] = event_assign.get(fmt, 0) + 1
        total = event_run_totals[fmt]
        if total > 1:
            run["displayName"] = f"{run['displayName']} — Run {event_assign[fmt]}"

    return sorted_runs


def build_data() -> dict:
    lines = read_lines(PREV_LOG_PATH) + read_lines(LOG_PATH)
    parsed = parse_log(lines)

    matches = parsed["matches"]
    wins = sum(1 for m in matches if m["result"] == "win")
    losses = sum(1 for m in matches if m["result"] == "loss")
    drafts = build_drafts(matches, parsed["deck_list_by_event"], parsed["deck_list_by_id"])

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
            cs = d.get("cardStats")
            colors = "/".join(f"{v}{k}" for k, v in cs["colors"].items()) if cs else "no card data"
            print(f"  {d['displayName']:35} {d['wins']}W-{d['losses']}L  {colors}")
    if data["inventory"]:
        inv = data["inventory"]
        print(f"Inventory: {inv['gold']}g {inv['gems']} gems | WC: {inv['wcCommon']}C {inv['wcUncommon']}U {inv['wcRare']}R {inv['wcMythic']}M")
