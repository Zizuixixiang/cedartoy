"""Authenticated Deeptalk boundary shared by the browser and root MCP."""
from deeptalk import catalog
from deeptalk.catalog import DeeptalkError
from deeptalk.store import Store, integer


def machines_for(user, db_connect):
    with db_connect() as conn:
        rows = conn.execute("""SELECT ai.id,ai.username FROM user_bindings b
            JOIN toy_users ai ON ai.id=b.ai_user_id
            WHERE b.human_user_id=? AND ai.is_ai=1 AND ai.deleted_at IS NULL
            ORDER BY ai.id""", (int(user["id"]),)).fetchall()
        return [dict(r) for r in rows]


def execute(user, action, params, *, db_path, db_connect, web=False):
    if not user or not user.get("id"):
        raise DeeptalkError("请先登录", 401)
    if bool(user.get("is_ai")) == web:
        raise DeeptalkError("网页仅供人类账号操作；MCP须使用小机自己的Token", 403)
    bound_human_ids = ()
    if user.get("is_ai"):
        with db_connect() as conn:
            bound_human_ids = tuple(r[0] for r in conn.execute("""SELECT b.human_user_id
                FROM user_bindings b JOIN toy_users h ON h.id=b.human_user_id
                JOIN toy_users ai ON ai.id=b.ai_user_id
                WHERE b.ai_user_id=? AND ai.is_ai=1 AND ai.deleted_at IS NULL
                AND h.is_ai=0 AND h.deleted_at IS NULL""", (int(user["id"]),)))
    store = Store(db_path, bound_human_ids=bound_human_ids)
    if action == "context" and web:
        return {"topics": catalog.topics(), "machines": machines_for(user, db_connect), "username": user["username"]}
    if action == "topics":
        return {"topics": catalog.topics()}
    if action == "source" and params.get("round_id"):
        source = store.source(user, params["round_id"])
        return catalog.public_source(source) if web else source
    if action in {"questions", "source"}:
        topic = catalog.load_topic(params.get("topic_id"))
        if action == "source":
            return catalog.public_source(topic) if web else {k: v for k, v in topic.items() if k != "questions"}
        number = integer(params.get("number", 1), "number", 1, len(topic["questions"]))
        limit = integer(params.get("limit", 1), "limit", 1, 100)
        return {"topic_id": topic["topic_id"], "title": catalog.DEEPTALK_TITLES[topic["topic_id"]], "total": len(topic["questions"]),
                "questions": [dict(number=i, id=q["id"], text=q["text"])
                              for i, q in enumerate(topic["questions"][number - 1:number - 1 + limit], number)]}
    if action == "create" and web:
        if "ai_id" in params:
            raise DeeptalkError("创建方式已更新，请刷新页面后开始", 409)
        if not machines_for(user, db_connect):
            raise DeeptalkError("请先回首页绑定小机，再开始问答", 403)
        return store.create(user, None, params.get("topic_id"), params.get("request_id"), sharing_mode="bound_shared", mode=params.get("mode", "blind"))
    if action == "history":
        return store.history(user, params.get("offset", 0), params.get("limit", 20))
    if action in {"status", "question", "result"} or (action == "view" and web):
        return store.read(user, params.get("round_id"), action=action,
                          number=params.get("number", 1), limit=params.get("limit", 1))
    if action == "answer":
        return store.answer(user, params)
    if action == "submit":
        return store.submit(user, params)
    raise DeeptalkError("未知操作，请查看 get_guide(deeptalk)")
