"""Account-owned favorites, stored alongside toy_users (never save-slot IDs)."""

from .display_titles import RESEARCH_TITLES, DEEPTALK_TITLES
from deeptalk.catalog import ALIASES

# Canonical IDs; homepage aliases are translated at the browser boundary.
GAME_NAMES = {
    "turtle_soup": "海龟汤",
    "autochess": "弈间",
    "duel": "双弈",
    "tarot": "塔罗占卜",
    "ai_life": "AI人生桌游",
    "detroit": "底特律：变人",
    "fishing": "AI钓鱼",
    "forest": "格林童话境遇",
    "bar": "空杯俱乐部",
    "eco": "瓶中生态",
    "ciyuwu": "词与物",
    "imitator_td": "植物大战丧尸随机版",
    "puzzle_box": "解谜盲盒",
    "memoria": "Memoria Station",
    "shared_craft": "共同手作间",
    "candle": "烛",
    "white_room": "白房间",
    "arcade": "街机厅",
    "burger": "午间汉堡铺",
    "market": "出门买菜上桌吃饭",
    "leek": "韭菜修炼之道",
    "delve": "下矿",
    "nowhere": "乌有乡",
    "travel": "旅行",
    "workkk": "AI打工人",
    "garden_cat": "花园与猫咪",
    "camping_plaza": "露营广场",
    "crucible_echoes": "坩埚余响",
    "moonlit": "月幕万象",
    "mbti": "MBTI 人格",
    "enneagram": "九型人格",
    "dnd": "九阵营",
    "love": "爱之语",
    "ecr": "依恋类型",
    "humanity": "人类浓度检测",
    "sins_virtues": "七宗罪 VS 七美德",
    "bdsmtest": "属性测试"
}
GAME_NAMES.update(RESEARCH_TITLES)
GAME_NAMES.update({game: DEEPTALK_TITLES[topic] for game, topic in ALIASES.items()})


def init_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_game_favorites (
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            game_id TEXT NOT NULL,
            PRIMARY KEY (user_id, game_id)
        )
    """)


def operate(conn, user, action, game, *, include_bound, error):
    if action not in ("favorites", "favorite_add", "favorite_remove"):
        raise error(-32602, "未知收藏操作；见 get_guide(game=\"list_games\")")
    if action != "favorites" and (not isinstance(game, str) or game not in GAME_NAMES):
        raise error(-32602, "未知游戏 ID；使用 list_games 或首页的游戏 ID")
    if action == "favorite_add":
        conn.execute("INSERT OR IGNORE INTO account_game_favorites (user_id, game_id) VALUES (?, ?)",
                     (user["id"], game))
    elif action == "favorite_remove":
        conn.execute("DELETE FROM account_game_favorites WHERE user_id = ? AND game_id = ?",
                     (user["id"], game))

    def collection(owner):
        rows = conn.execute("SELECT game_id FROM account_game_favorites WHERE user_id = ? ORDER BY game_id",
                            (owner["id"],)).fetchall()
        return {"username": owner["username"], "games": [
            {"id": row["game_id"], "name": GAME_NAMES[row["game_id"]]}
            for row in rows if row["game_id"] in GAME_NAMES
        ]}

    result = {"self": collection(user)}
    if include_bound and user["is_ai"] and action == "favorites":
        # One direct edge only. A shared human never exposes their other machines.
        humans = conn.execute("""
            SELECT DISTINCT u.id, u.username FROM user_bindings b
            JOIN toy_users u ON u.id = b.human_user_id
            WHERE b.ai_user_id = ? AND u.is_ai = 0 AND u.deleted_at IS NULL
            ORDER BY u.id
        """, (user["id"],)).fetchall()
        if humans:
            result["humans"] = [collection(human) for human in humans]
    return result
