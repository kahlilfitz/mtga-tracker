import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import initSqlJs, { Database as SqlJsDatabase } from "sql.js";

const isWindows = process.platform === "win32";

const LOG_DIR = isWindows
  ? path.join(os.homedir(), "AppData/LocalLow/Wizards Of The Coast/MTGA")
  : path.join(os.homedir(), "Library/Logs/Wizards Of The Coast/MTGA");

const LOG_PATH = path.join(LOG_DIR, "Player.log");
const PREV_LOG_PATH = path.join(LOG_DIR, "Player-prev.log");
const CARD_DB_DIR = isWindows
  ? path.join(os.homedir(), "AppData/LocalLow/Wizards Of The Coast/MTGA/Downloads/Raw")
  : path.join(os.homedir(), "Library/Application Support/com.wizards.mtga/Downloads/Raw");

const ARENA_LIMITED_PREFIX = "ArenaLimited";

const MATCH_EVENT_RE = /\[UnityCrossThreadLogger\].*?: Match to (\S+): (\w+)/;
const REQUEST_RE = /\[UnityCrossThreadLogger\]==>\s+(\w+)\s+(\{.*)/;

const COLOR_MAP: Record<string, string> = { "1": "W", "2": "U", "3": "B", "4": "R", "5": "G" };
const RARITY_MAP: Record<number, string> = { 1: "land", 2: "common", 3: "uncommon", 4: "rare", 5: "mythic" };

function isDraftFormat(fmt: string): boolean {
  return fmt.includes("Draft") || fmt.includes("Sealed") || fmt.startsWith(ARENA_LIMITED_PREFIX);
}

function formatDisplay(fmt: string): string {
  if (!fmt) return "Unknown";
  let parts = fmt.split("_");
  if (parts.length && /^\d{8}$/.test(parts[parts.length - 1])) {
    parts = parts.slice(0, -1);
  }
  let label = parts[0].replace(/(?<=[a-z])(?=[A-Z])/g, " ");
  if (parts.length > 1 && !/^\d+$/.test(parts[1])) {
    label += ` · ${parts[1]}`;
  }
  return label;
}

function readLines(filePath: string): string[] {
  if (!fs.existsSync(filePath)) return [];
  const content = fs.readFileSync(filePath, { encoding: "utf-8" });
  return content.split("\n");
}

function findCardDb(): string | null {
  if (!fs.existsSync(CARD_DB_DIR)) return null;
  const files = fs.readdirSync(CARD_DB_DIR).filter((f) => /^Raw_CardDatabase_.*\.mtga$/.test(f));
  if (!files.length) return null;
  return path.join(CARD_DB_DIR, files[0]);
}

interface CardInfo {
  colors: string[];
  cmc: number;
  isToken: boolean;
  rarity: string;
  name: string;
}

let sqlJsPromise: ReturnType<typeof initSqlJs> | null = null;
function getSqlJs() {
  if (!sqlJsPromise) {
    sqlJsPromise = initSqlJs({
      locateFile: (file: string) => path.join(__dirname, "..", "node_modules", "sql.js", "dist", file),
    });
  }
  return sqlJsPromise;
}

async function loadCardData(cardIds: Set<number>): Promise<Map<number, CardInfo>> {
  const result = new Map<number, CardInfo>();
  const dbPath = findCardDb();
  if (!dbPath || cardIds.size === 0) return result;

  let db: SqlJsDatabase | null = null;
  try {
    const SQL = await getSqlJs();
    db = new SQL.Database(fs.readFileSync(dbPath));
    const ids = Array.from(cardIds);
    const placeholders = ids.map(() => "?").join(",");
    const stmt = db.prepare(
      `SELECT c.GrpId AS GrpId, c.Colors AS Colors, c.Order_CMCWithXLast AS Cmc, c.IsToken AS IsToken, c.Rarity AS Rarity, l.Loc AS Loc
       FROM Cards c
       LEFT JOIN Localizations_enUS l ON l.LocId=c.TitleId AND l.Formatted=1
       WHERE c.GrpId IN (${placeholders})`
    );
    stmt.bind(ids);
    while (stmt.step()) {
      const row = stmt.getAsObject() as any;
      const colorVals = String(row.Colors ?? "").split(",").map((c) => c.trim()).filter(Boolean);
      const colors = colorVals.filter((c) => c in COLOR_MAP).map((c) => COLOR_MAP[c]);
      const name = String(row.Loc ?? "").replace(/<[^>]+>/g, "") || `Card #${row.GrpId}`;
      result.set(row.GrpId, {
        colors,
        cmc: row.Cmc || 0,
        isToken: !!row.IsToken,
        rarity: RARITY_MAP[row.Rarity] ?? "common",
        name,
      });
    }
    stmt.free();
  } catch {
    // ignore — no card data available
  } finally {
    db?.close();
  }
  return result;
}

interface CurveCard { name: string; qty: number; rarity?: string }
interface CurveBucket { total: number; rarities: Record<string, number>; cards: CurveCard[] }
interface DeckStats { colors: Record<string, number>; curve: Record<string, CurveBucket> }

async function deckStats(deck: Record<number, number>): Promise<DeckStats> {
  const allIds = new Set(Object.keys(deck).map(Number));
  const cardData = await loadCardData(allIds);

  const colorCounts: Record<string, number> = { W: 0, U: 0, B: 0, R: 0, G: 0 };
  const curveDetail = new Map<number, Record<string, CurveCard[]>>();

  for (const [cardIdStr, qty] of Object.entries(deck)) {
    const cardId = Number(cardIdStr);
    const info = cardData.get(cardId);
    if (!info || info.isToken) continue;
    for (const color of info.colors) {
      if (color in colorCounts) colorCounts[color] += qty;
    }
    const cmc = info.cmc;
    const rarity = info.rarity;
    if (rarity === "land" && cmc === 0 && info.colors.length === 0) continue;
    const bucket = Math.min(cmc, 7);
    if (!curveDetail.has(bucket)) {
      curveDetail.set(bucket, { mythic: [], rare: [], uncommon: [], common: [], land: [] });
    }
    curveDetail.get(bucket)![rarity].push({ name: info.name, qty });
  }

  const colors: Record<string, number> = {};
  for (const [k, v] of Object.entries(colorCounts)) {
    if (v > 0) colors[k] = v;
  }

  const curve: Record<string, CurveBucket> = {};
  const sortedBuckets = Array.from(curveDetail.keys()).sort((a, b) => a - b);
  for (const bucket of sortedBuckets) {
    const rarities = curveDetail.get(bucket)!;
    let total = 0;
    const rarityTotals: Record<string, number> = {};
    for (const [r, cards] of Object.entries(rarities)) {
      const sum = cards.reduce((s, c) => s + c.qty, 0);
      total += sum;
      if (cards.length) rarityTotals[r] = sum;
    }
    const allCards: CurveCard[] = [];
    for (const r of ["mythic", "rare", "uncommon", "common", "land"]) {
      for (const c of rarities[r] || []) {
        allCards.push({ name: c.name, qty: c.qty, rarity: r });
      }
    }
    curve[String(bucket)] = { total, rarities: rarityTotals, cards: allCards };
  }

  return { colors, curve };
}

interface Match {
  matchId: string;
  format: string;
  deckName: string;
  deckId: string;
  opponentName: string;
  myTeamId: number | null;
  timestamp: string;
  result: string;
}

interface ParsedLog {
  myPlayerId: string | null;
  matches: Match[];
  rank: any;
  inventory: any;
  deckListByEvent: Record<string, Record<number, number>>;
  deckListById: Record<string, Record<number, number>>;
}

function parseLog(lines: string[]): ParsedLog {
  let myPlayerId: string | null = null;
  const matches: Match[] = [];
  let rank: any = null;
  let inventory: any = null;

  let activeMatch: Partial<Match> = {};
  let pendingDeckName = "";
  let pendingDeckId = "";
  let pendingDeckList: Record<number, number> = {};
  const deckByEvent: Record<string, string> = {};
  const deckIdByEvent: Record<string, string> = {};
  const deckListByEvent: Record<string, Record<number, number>> = {};
  const deckListById: Record<string, Record<number, number>> = {};

  let i = 0;
  while (i < lines.length) {
    const line = lines[i].replace(/\s+$/, "");

    const m = MATCH_EVENT_RE.exec(line);
    if (m) {
      const targetPlayer = m[1];
      const eventName = m[2];
      let j = i + 1;
      while (j < lines.length && !lines[j].trim().startsWith("{")) {
        j++;
      }
      let payload: any = null;
      if (j < lines.length) {
        try {
          payload = JSON.parse(lines[j].trim());
        } catch {
          // ignore malformed JSON
        }
      }

      if (payload && eventName === "MatchGameRoomStateChangedEvent") {
        const gri = payload.matchGameRoomStateChangedEvent?.gameRoomInfo ?? {};
        const config = gri.gameRoomConfig ?? {};
        const stateType = gri.stateType ?? "";
        const players: any[] = config.reservedPlayers ?? [];
        const matchId = config.matchId ?? "";
        let eventId = "";

        if (!myPlayerId && targetPlayer) {
          myPlayerId = targetPlayer;
        }

        if (stateType === "MatchGameRoomStateType_Playing") {
          let myTeam: number | null = null;
          let opponentName = "";
          for (const p of players) {
            if (p.userId === myPlayerId) {
              myTeam = p.teamId;
              eventId = p.eventId ?? "";
            } else {
              opponentName = p.playerName ?? "";
            }
          }

          const deckName = pendingDeckName || deckByEvent[eventId] || "";
          const deckId = pendingDeckId || deckIdByEvent[eventId] || "";
          const deckList = Object.keys(pendingDeckList).length ? pendingDeckList : (deckListByEvent[eventId] || {});
          if (deckName && eventId) deckByEvent[eventId] = deckName;
          if (deckId && eventId) deckIdByEvent[eventId] = deckId;
          if (Object.keys(deckList).length && eventId) deckListByEvent[eventId] = deckList;
          if (deckId && Object.keys(deckList).length) deckListById[deckId] = deckList;

          activeMatch = {
            matchId,
            format: eventId,
            deckName,
            deckId,
            opponentName,
            myTeamId: myTeam,
            timestamp: payload.timestamp ?? "",
          };
          pendingDeckName = "";
          pendingDeckId = "";
          pendingDeckList = {};
        } else if (stateType === "MatchGameRoomStateType_MatchCompleted") {
          // If we never saw the Playing state for this match (e.g. a forced
          // draw on reconnect), recover what we can from reservedPlayers.
          let myTeamId = activeMatch.myTeamId ?? null;
          let format = activeMatch.format ?? "";
          let opponentName = activeMatch.opponentName ?? "";
          if (myTeamId === null || !format || !opponentName) {
            for (const p of players) {
              if (p.userId === myPlayerId) {
                if (myTeamId === null) myTeamId = p.teamId ?? null;
                if (!format) format = p.eventId ?? "";
              } else if (!opponentName) {
                opponentName = p.playerName ?? "";
              }
            }
          }

          const resultList: any[] = gri.finalMatchResult?.resultList ?? [];
          let result = "unknown";
          for (const r of resultList) {
            if (r.scope === "MatchScope_Match") {
              if (r.result === "ResultType_Draw" || r.winningTeamId === undefined) {
                result = "draw";
              } else {
                result = r.winningTeamId === myTeamId ? "win" : "loss";
              }
              break;
            }
          }
          if (result === "unknown") {
            for (const r of resultList) {
              if (r.scope === "MatchScope_Game") {
                if (r.result === "ResultType_Draw" || r.winningTeamId === undefined) {
                  result = "draw";
                } else {
                  result = r.winningTeamId === myTeamId ? "win" : "loss";
                }
                break;
              }
            }
          }

          const record: Match = {
            matchId: matchId || activeMatch.matchId || "",
            format,
            deckName: activeMatch.deckName ?? "",
            deckId: activeMatch.deckId ?? "",
            opponentName,
            myTeamId,
            timestamp: activeMatch.timestamp ?? "",
            result,
          };
          matches.push(record);
          activeMatch = {};
        }
      }

      i = j + 1;
      continue;
    }

    const reqM = REQUEST_RE.exec(line);
    if (reqM) {
      const en = reqM[1];
      if (en === "EventSetDeckV3") {
        try {
          const outer = JSON.parse(reqM[2]);
          const req = JSON.parse(outer.request ?? "{}");
          const summary = req.Summary ?? {};
          const name = summary.Name ?? "";
          const mtgaEvent = req.EventName ?? "";
          const rawDeck = req.Deck ?? {};
          const mainDeck: any[] = rawDeck.MainDeck ?? [];
          const deckList: Record<number, number> = {};
          for (const entry of mainDeck) {
            if ("cardId" in entry) deckList[entry.cardId] = entry.quantity;
          }
          const dId = summary.DeckId ?? "";
          if (name) {
            pendingDeckName = name;
            if (mtgaEvent) deckByEvent[mtgaEvent] = name;
          }
          if (dId) {
            pendingDeckId = dId;
            if (mtgaEvent) deckIdByEvent[mtgaEvent] = dId;
          }
          if (Object.keys(deckList).length) {
            pendingDeckList = deckList;
            if (mtgaEvent) deckListByEvent[mtgaEvent] = deckList;
            if (dId) deckListById[dId] = deckList;
          }
        } catch {
          // ignore malformed JSON
        }
      }
      i++;
      continue;
    }

    if (line.trim().startsWith('{"InventoryInfo"')) {
      try {
        const payload = JSON.parse(line.trim());
        const invInfo = payload.InventoryInfo;
        if (invInfo) {
          inventory = {
            gold: invInfo.Gold ?? 0,
            gems: invInfo.Gems ?? 0,
            wcCommon: invInfo.WildCardCommons ?? 0,
            wcUncommon: invInfo.WildCardUnCommons ?? 0,
            wcRare: invInfo.WildCardRares ?? 0,
            wcMythic: invInfo.WildCardMythics ?? 0,
          };
        }
      } catch {
        // ignore malformed JSON
      }
    }

    i++;
  }

  // Direct scan for rank and inventory
  for (const line of lines) {
    const stripped = line.trim();
    if (!stripped.startsWith("{")) continue;
    let payload: any;
    try {
      payload = JSON.parse(stripped);
    } catch {
      continue;
    }
    if (typeof payload !== "object" || payload === null) continue;

    if ("constructedClass" in payload) {
      rank = {
        constructed: {
          class: payload.constructedClass ?? "",
          level: payload.constructedLevel ?? 0,
          step: payload.constructedStep ?? 0,
          wins: payload.constructedMatchesWon ?? 0,
          losses: payload.constructedMatchesLost ?? 0,
        },
        limited: {
          class: payload.limitedClass ?? "Bronze",
          level: payload.limitedLevel ?? 1,
          step: payload.limitedStep ?? 0,
          wins: payload.limitedMatchesWon ?? 0,
          losses: payload.limitedMatchesLost ?? 0,
        },
      };
    }
    const invInfo = payload.InventoryInfo;
    if (invInfo) {
      inventory = {
        gold: invInfo.Gold ?? 0,
        gems: invInfo.Gems ?? 0,
        wcCommon: invInfo.WildCardCommons ?? 0,
        wcUncommon: invInfo.WildCardUnCommons ?? 0,
        wcRare: invInfo.WildCardRares ?? 0,
        wcMythic: invInfo.WildCardMythics ?? 0,
      };
    }
  }

  return { myPlayerId, matches, rank, inventory, deckListByEvent, deckListById };
}

function longestStreak(matches: Match[]): { win: number; loss: number } {
  const best = { win: 0, loss: 0 };
  const cur = { win: 0, loss: 0 };
  for (const m of matches) {
    if (m.result === "win") {
      cur.win += 1;
      cur.loss = 0;
    } else {
      cur.loss += 1;
      cur.win = 0;
    }
    best.win = Math.max(best.win, cur.win);
    best.loss = Math.max(best.loss, cur.loss);
  }
  return best;
}

interface DraftRun {
  eventId: string;
  deckId: string;
  displayName: string;
  deckName: string;
  wins: number;
  losses: number;
  matches: Match[];
  cardStats: DeckStats | null;
  bestWinStreak: number;
  worstLossStreak: number;
}

async function buildDrafts(
  matches: Match[],
  deckListByEvent: Record<string, Record<number, number>>,
  deckListById: Record<string, Record<number, number>>
): Promise<DraftRun[]> {
  const runs = new Map<string, DraftRun>();
  const runOrder: string[] = [];

  for (const m of matches) {
    const fmt = m.format ?? "";
    if (!isDraftFormat(fmt) || !m.deckId) continue;
    const deckId = m.deckId;
    const key = `${fmt} ${deckId}`;
    if (!runs.has(key)) {
      runs.set(key, {
        eventId: fmt,
        deckId,
        displayName: formatDisplay(fmt),
        deckName: m.deckName ?? "",
        wins: 0,
        losses: 0,
        matches: [],
        cardStats: null,
        bestWinStreak: 0,
        worstLossStreak: 0,
      });
      runOrder.push(key);
    }
    const r = runs.get(key)!;
    if (m.deckName && !r.deckName) r.deckName = m.deckName;
    if (m.result === "win") r.wins += 1;
    else if (m.result === "loss") r.losses += 1;
    r.matches.push(m);
  }

  for (const [key, run] of runs) {
    const [fmt, deckId] = key.split(" ");
    const deckList = deckListById[deckId] || deckListByEvent[fmt] || {};
    run.cardStats = Object.keys(deckList).length ? await deckStats(deckList) : null;
    const streak = longestStreak(run.matches);
    run.bestWinStreak = streak.win;
    run.worstLossStreak = streak.loss;
  }

  const sortedRuns = Array.from(runs.values()).sort((a, b) => {
    const ta = a.matches[0]?.timestamp ?? "";
    const tb = b.matches[0]?.timestamp ?? "";
    return tb.localeCompare(ta);
  });

  const eventRunTotals = new Map<string, number>();
  for (const run of sortedRuns) {
    eventRunTotals.set(run.eventId, (eventRunTotals.get(run.eventId) ?? 0) + 1);
  }

  const eventAssign = new Map<string, number>();
  for (const run of [...sortedRuns].reverse()) {
    const fmt = run.eventId;
    eventAssign.set(fmt, (eventAssign.get(fmt) ?? 0) + 1);
    const total = eventRunTotals.get(fmt) ?? 0;
    if (total > 1) {
      run.displayName = `${run.displayName} — Run ${eventAssign.get(fmt)}`;
    }
  }

  return sortedRuns;
}

export async function buildData(): Promise<any> {
  const lines = [...readLines(PREV_LOG_PATH), ...readLines(LOG_PATH)];
  const parsed = parseLog(lines);

  const matches = parsed.matches;
  const wins = matches.filter((m) => m.result === "win").length;
  const losses = matches.filter((m) => m.result === "loss").length;
  const drafts = await buildDrafts(matches, parsed.deckListByEvent, parsed.deckListById);

  return {
    generated: new Date().toISOString(),
    detailedLogsEnabled: !!(matches.length || parsed.rank || parsed.inventory),
    summary: {
      wins,
      losses,
      total: matches.length,
      winRate: matches.length ? Math.round((wins / matches.length) * 1000) / 10 : 0,
    },
    rank: parsed.rank,
    inventory: parsed.inventory,
    matches: matches.slice(-100),
    drafts,
  };
}
