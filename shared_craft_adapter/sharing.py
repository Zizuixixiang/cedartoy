"""Explicit, immutable submissions. Historical pending work stays private."""
import sqlite3
import time
import uuid
from contextlib import closing

from .identity import author_access
from .object_storage import file_identity, reference_sha, StorageUnavailable


class Shares:
    def __init__(self, path, grants_db, images=None):
        self.path, self.grants_db = path, grants_db
        self.images = images
        with closing(self.connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY, room TEXT NOT NULL, item TEXT NOT NULL,
                actor TEXT NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL,
                image BLOB NOT NULL, status TEXT NOT NULL
                CHECK(status IN ('pending','shared','declined')),
                created INTEGER NOT NULL, decided INTEGER,
                UNIQUE(room,item,actor,note))""")

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def submit(self, room, identity, item_id, note="", confirm=False):
        # Calling submit is the opt-in; accept legacy confirm without requiring it.
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("附言最多500字")
        item = next((i for i in room.gallery.list_items() if i["id"] == item_id), None)
        if not item:
            raise ValueError("已完成作品不存在")
        contributors = item.get("actor_ids", [])
        if identity["is_ai"] and identity["actor"] not in contributors:
            raise PermissionError("只能发起自己参与创作的作品分享")
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("""SELECT id,status FROM submissions
                WHERE room=? AND item=? AND actor=? AND note=?""",
                (identity["room"], item_id, identity["actor"], note)).fetchone()
            if previous:
                result = dict(previous)
                if result["status"] == "pending":
                    # Only an explicit repeat by this same participating actor
                    # releases this exact frozen snapshot. Never migrate rows
                    # on startup/read, or replace their image with a live one.
                    conn.execute("UPDATE submissions SET status='shared',decided=? WHERE id=?",
                                 (int(time.time()), result["id"]))
                    result["status"] = "shared"
                return result
            path = room.gallery.image_path(item_id)
            image = path.read_bytes()
            if reference_sha(image):
                if self.images is None:
                    raise StorageUnavailable("R2 主存图片需要配置读取后端")
                image = self.images.read(*file_identity(self.images.root, path), image)
            submission = uuid.uuid4().hex
            stored = (self.images.store(identity["room"], "shares", submission, image)
                      if self.images is not None else image)
            conn.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (submission, identity["room"], item_id, identity["actor"], item["title"],
                          note, stored, "shared", int(time.time()), int(time.time())))
        # Primary references commit only after verified upload; legacy mirrors queue after commit.
        if self.images is not None:
            try:
                self.images.mirror(identity["room"], "shares", submission, image)
            except Exception:
                self.images._failure()
        return {"id": submission, "status": "shared"}

    def _image(self, row):
        if self.images is None:
            if reference_sha(row["image"]):
                raise StorageUnavailable("R2 主存图片需要配置读取后端")
            return row["image"]
        return self.images.read(row["room"], "shares", row["id"], row["image"])

    def pending(self, identity):
        if identity["is_ai"]:
            return []
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT id,item,title,note FROM submissions WHERE room=? AND status='pending' ORDER BY created",
                (identity["room"],))]

    def decide(self, identity, submission, confirm):
        if identity["is_ai"]:
            raise PermissionError("共同作品分享需要人类确认")
        if type(confirm) is not bool:
            raise ValueError("confirm 必须为布尔值")
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM submissions WHERE id=? AND room=?",
                               (submission, identity["room"])).fetchone()
            if not row:
                raise PermissionError("无权确认该作品")
            status = "shared" if confirm else "declined"
            if row["status"] == "pending":
                conn.execute("UPDATE submissions SET status=?,decided=? WHERE id=?",
                             (status, int(time.time()), submission))
            else:
                status = row["status"]
            return {"id": submission, "status": status}

    def pending_image(self, identity, submission):
        if identity["is_ai"]:
            raise PermissionError("仅所属人类可确认")
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT id,room,image FROM submissions WHERE id=? AND room=? AND status='pending'",
                               (submission, identity["room"])).fetchone()
            if not row:
                raise PermissionError("无权查看")
            return self._image(row)

    def read(self, user_id, page=1, image_id=None):
        if not author_access(self.grants_db, user_id):
            raise PermissionError("仅本作已授权作者可查看")
        with closing(self.connect()) as conn:
            if image_id:
                row = conn.execute("SELECT id,room,image FROM submissions WHERE id=? AND status='shared'",
                                   (image_id,)).fetchone()
                if not row:
                    raise PermissionError("作品不可用")
                return self._image(row)
            page = int(page)
            if page < 1:
                raise ValueError("page 必须为正数")
            total = conn.execute("SELECT count(*) FROM submissions WHERE status='shared'").fetchone()[0]
            pages = max(1, (total + 9) // 10)
            page = min(page, pages)
            items = [dict(row) for row in conn.execute(
                """SELECT id,title,note,decided AS created_at FROM submissions
                WHERE status='shared' ORDER BY decided DESC,id LIMIT 10 OFFSET ?""", ((page-1)*10,))]
            return {"items": items, "page": page, "pages": pages, "total": total,
                    "has_prev": page > 1, "has_next": page < pages}
