"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {spawnSync} = require("node:child_process");
const {JSDOM, VirtualConsole} = require("jsdom");
const csstree = require("css-tree");

const root = path.resolve(__dirname, "..");
const rendered = spawnSync(
  "python3",
  [
    "-c",
    "from pathlib import Path; from tarot_adapter import WEB; "
      + "print(WEB.homepage_index(Path('index.html').read_text(encoding='utf-8')).decode('utf-8'))",
  ],
  {cwd: root, encoding: "utf8"},
);
assert.equal(rendered.status, 0, rendered.stderr);
const homepage = rendered.stdout;
const styleMatch = homepage.match(/<style>([\s\S]*?)<\/style>/);
assert.ok(styleMatch, "rendered homepage keeps its original stylesheet");
csstree.parse(styleMatch[1]);
assert.match(homepage, /id: "tarot"/);
assert.match(homepage, /watchLabel: "开始占问 →"/);
assert.doesNotMatch(homepage, /tarotInviteModal|tarotInviteState/);
assert.doesNotMatch(homepage, /\/api\/tarot\/invitations\/pending|X-Tarot-CSRF/);
assert.match(
  homepage,
  /\$\("notificationBell"\)\.addEventListener\("click", openAnnouncementList\)/,
  "homepage bell must remain the normal site-notification bell",
);

const uiScripts = ["v3", "v4", "v5", "v7"].map(version => (
  fs.readFileSync(path.join(root, `assets/tarot/managed-ui.${version}.js`), "utf8")
));

function response(body, ok = true, status = ok ? 200 : 400) {
  return {ok, status, json: async () => body};
}

function invite(sessionId, machineName, question) {
  return {
    session_id: sessionId,
    machine_name: machineName,
    question,
    expires_at: 1_900_000_000,
    csrf_token: `csrf-${sessionId.slice(0, 4)}`,
  };
}

async function waitFor(window, predicate, message, timeoutMs = 1800) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(resolve => window.setTimeout(resolve, 10));
  }
  assert.fail(message);
}

function makeInvitationApi(window, requests) {
  const state = {
    ownerId: 101,
    pending: [],
    version: 0,
    current: {
      id: "current_session",
      phase: "accepted",
      draws: [],
      reading: null,
    },
    timeoutIds: new Set(),
  };
  const waiters = [];
  const cursor = () => state.version.toString(16).padStart(64, "0");
  const snapshot = () => ({
    human_user_id: state.ownerId,
    invitations: state.pending.map(item => ({...item})),
    cursor: cursor(),
  });
  const removeWaiter = waiter => {
    const index = waiters.indexOf(waiter);
    if (index >= 0) waiters.splice(index, 1);
  };

  function publish(pending) {
    state.pending = pending.map(item => ({...item}));
    state.version += 1;
    for (const waiter of [...waiters]) {
      removeWaiter(waiter);
      waiter.resolve(response(snapshot()));
    }
  }

  async function fetch(url, options = {}) {
    requests.push({url, options});
    const parsed = new URL(url, "https://toy.example/");
    if (parsed.pathname === "/api/tarot/models/status") {
      return response({
        models: [
          {model: "gemini-3.5-flash", status: "available", remaining_seconds: 0},
          {model: "gemini-3.1-pro-preview", status: "available", remaining_seconds: 0},
        ],
      });
    }
    if (parsed.pathname === "/api/tarot/history") {
      return response({items: [], next_offset: null});
    }
    if (parsed.pathname === "/api/tarot/invitations/pending") {
      const after = parsed.searchParams.get("cursor");
      const waitSeconds = Number(parsed.searchParams.get("wait_seconds") || 0);
      if (!after || after !== cursor() || waitSeconds <= 0) return response(snapshot());
      return new Promise((resolve, reject) => {
        const waiter = {resolve, reject};
        waiters.push(waiter);
        options.signal?.addEventListener("abort", () => {
          removeWaiter(waiter);
          reject(new window.DOMException("Aborted", "AbortError"));
        }, {once: true});
      });
    }
    if (parsed.pathname === "/companion/v1/sessions/current_session") {
      return response({csrf_token: "current-csrf", session: {...state.current}});
    }
    const action = parsed.pathname.match(
      /^\/api\/tarot\/invitations\/([A-Za-z0-9_-]{32,128})\/(accept|reject)$/,
    );
    if (action) {
      if (state.timeoutIds.has(action[1])) {
        throw new window.DOMException("Timed out", "AbortError");
      }
      const item = state.pending.find(candidate => candidate.session_id === action[1]);
      if (!item) return response({error: "邀请已处理"}, false, 409);
      if (options.headers?.["X-Tarot-CSRF"] !== item.csrf_token) {
        return response({error: "CSRF 校验失败"}, false, 403);
      }
      publish(state.pending.filter(candidate => candidate.session_id !== action[1]));
      return response({
        invitation_state: action[2] === "accept" ? "accepted" : "rejected",
      });
    }
    throw new Error(`unexpected request: ${url}`);
  }

  return {
    fetch,
    publish,
    dropSilently(sessionId) {
      state.pending = state.pending.filter(item => item.session_id !== sessionId);
      state.version += 1;
    },
    setTimedOut(sessionId, enabled) {
      if (enabled) state.timeoutIds.add(sessionId);
      else state.timeoutIds.delete(sessionId);
    },
    state,
    waiting: () => waiters.length,
  };
}

function pageHtml() {
  return `<!doctype html><html><body>
    <header id="topbar"><div class="top-right">
      <button id="providerOrb"><span id="providerLabel"></span></button>
    </div></header>
    <aside id="settingsPanel" class="open"><div id="providerList"></div>
      <button id="settingsClose">关闭</button></aside>
    <main id="ui">
      <section id="phase-question" class="phase"><textarea id="questionInput"></textarea></section>
      <section id="phase-spread" class="phase hidden"></section>
      <div id="ritualBar" class="hidden"></div>
      <section id="photoPanel" class="phase hidden"></section>
      <aside id="readingPanel" class="hidden">
        <div class="reading-head"><h2 id="readingTitle">解读</h2>
          <p id="readingQuestion"></p><div id="readingChips"></div></div>
        <div id="readingStream"></div><button id="newReadBtn">新的占问</button>
      </aside>
      <div id="cardDetail" class="hidden"></div>
      <div class="panel"><p id="companionStatus">会话已就绪；抽牌将保存到本次记录。</p>
        <button>返回聊天</button><button>停止本次</button></div>
    </main>
    <div id="toasts"></div>
    <script type="application/json" id="companion-config">{"protocol":"cove-tarot-companion-v1","sessionId":"current_session","apiBase":"/companion/v1","humanUserId":101}</script>
  </body></html>`;
}

async function runGamePage() {
  const requests = [];
  let api;
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", error => {
    if (!/Not implemented: navigation/.test(error.message)) throw error;
  });
  const dom = new JSDOM(pageHtml(), {
    runScripts: "outside-only",
    pretendToBeVisual: true,
    url: "https://toy.example/tarot/session/current_session/",
    virtualConsole,
  });
  const {window} = dom;
  window.__hidden = false;
  Object.defineProperty(window.Document.prototype, "hidden", {
    configurable: true,
    get() { return window.__hidden; },
  });
  api = makeInvitationApi(window, requests);
  window.fetch = api.fetch;
  for (const source of uiScripts) window.eval(source);

  try {
    const byId = id => window.document.getElementById(id);
    const firstId = "A".repeat(32);
    const secondId = "B".repeat(32);
    await waitFor(window, () => api.waiting() === 1, "game page establishes one bounded invitation wait");
    assert.equal(byId("managedInviteModal").classList.contains("open"), false);

    api.publish([
      invite(firstId, "<b>甲小机</b>", "<img src=x onerror=alert(1)>\n第一个问题"),
      invite(secondId, "乙小机", "第二个问题"),
    ]);
    await waitFor(
      window,
      () => byId("managedInvitePendingList")?.children.length === 2,
      "pending invitations appear in the existing history panel state",
    );
    assert.equal(
      byId("managedInviteModal").classList.contains("open"),
      false,
      "settings overlay prevents an automatic invitation popup",
    );
    assert.equal(byId("managedInvitePendingList").querySelector("img"), null);
    assert.match(byId("managedInvitePendingList").textContent, /<img src=x/);

    byId("settingsPanel").classList.remove("open");
    await waitFor(
      window,
      () => byId("managedInviteModal").classList.contains("open"),
      "oldest queued invitation opens when the game returns to an idle question phase",
    );
    assert.equal(byId("managedInviteMachine").textContent, "<b>甲小机</b> 想问");
    assert.equal(byId("managedInviteQuestion").querySelector("img"), null);
    assert.match(byId("managedInviteQuestion").textContent, /第一个问题/);
    byId("managedInviteModal").querySelector(".managed-invite-later").click();
    assert.equal(byId("managedInviteModal").classList.contains("open"), false);

    byId("managedHistoryTrigger").click();
    await waitFor(
      window,
      () => byId("managedHistoryPanel").classList.contains("open"),
      "record panel opens without a new navigation entry",
    );
    assert.equal(byId("managedInvitePending").hidden, false);
    const firstRow = byId("managedInvitePendingList").children[0];
    const reject = firstRow.querySelector(".managed-invite-reject");
    reject.click();
    reject.click();
    await waitFor(
      window,
      () => byId("managedInvitePendingList").children.length === 1,
      "reject updates modal and record-panel state exactly once",
    );
    const rejects = requests.filter(({url}) => url.endsWith(`/${firstId}/reject`));
    assert.equal(rejects.length, 1, "rapid reject clicks are de-duplicated");
    assert.equal(rejects[0].options.credentials, "same-origin");
    assert.equal(rejects[0].options.headers["X-Tarot-CSRF"], "csrf-AAAA");
    await waitFor(
      window,
      () => byId("managedInvitePendingStatus").textContent === ""
        && byId("toasts").querySelector("[data-managed-invite-toast='1']"),
      "successful rejection clears the persistent notice and shows a short toast",
    );
    assert.equal(byId("managedInvitePending").hidden, false);
    assert.match(byId("managedInvitePendingList").textContent, /乙小机/);
    assert.match(byId("managedInvitePendingList").textContent, /第二个问题/);
    assert.equal(
      byId("toasts").querySelector("[data-managed-invite-toast='1']").textContent,
      "已拒绝",
    );

    byId("questionInput").value = "尚未完成的本地问题";
    byId("managedInvitePendingList").children[0]
      .querySelector(".managed-invite-accept").click();
    await waitFor(
      window,
      () => /尚未完成抽牌/.test(byId("managedInvitePendingStatus").textContent),
      "accept refuses to abandon an unsaved current question",
    );
    assert.equal(
      requests.filter(({url}) => url.endsWith(`/${secondId}/accept`)).length,
      0,
    );

    byId("questionInput").value = "";
    byId("readingStream").classList.add("streaming");
    byId("managedInvitePendingList").children[0]
      .querySelector(".managed-invite-accept").click();
    await waitFor(
      window,
      () => /当前解读仍在进行/.test(byId("managedInvitePendingStatus").textContent),
      "accept does not interrupt a streaming reading",
    );
    assert.equal(
      requests.filter(({url}) => url.endsWith(`/${secondId}/accept`)).length,
      0,
    );
    byId("readingStream").classList.remove("streaming");

    const staleId = "E".repeat(32);
    api.publish([
      invite(secondId, "乙小机", "第二个问题"),
      invite(staleId, "已处理小机", "另一页已经处理"),
    ]);
    await waitFor(
      window,
      () => byId("managedInvitePendingList").children.length === 2,
      "a second queued item reaches the shared pending list",
    );
    const staleRow = byId("managedInvitePendingList").children[1];
    api.setTimedOut(staleId, true);
    staleRow.querySelector(".managed-invite-reject").click();
    await waitFor(
      window,
      () => /邀请处理超时/.test(byId("managedInvitePendingStatus").textContent),
      "a timed-out response releases the busy state with a clear explanation",
    );
    assert.equal(byId("managedInvitePendingList").children.length, 2);
    api.setTimedOut(staleId, false);
    const staleRetryRow = byId("managedInvitePendingList").children[1];
    api.dropSilently(staleId);
    staleRetryRow.querySelector(".managed-invite-reject").click();
    await waitFor(
      window,
      () => byId("managedInvitePendingList").children.length === 1,
      "already-processed or expired responses refresh the shared pending list",
    );
    assert.match(
      byId("managedInvitePendingStatus").textContent,
      /其他页面处理或已过期/,
      "already-processed or expired responses keep a friendly explanation",
    );

    byId("managedInvitePendingList").children[0]
      .querySelector(".managed-invite-reject").click();
    await waitFor(
      window,
      () => byId("managedInvitePending").hidden
        && byId("managedInvitePendingList").children.length === 0
        && byId("managedInvitePendingStatus").textContent === "",
      "rejecting the final invitation immediately collapses the empty section",
    );
    assert.equal(
      byId("toasts").querySelector("[data-managed-invite-toast='1']")?.textContent,
      "已拒绝",
    );
    byId("managedHistoryClose").click();
    await waitFor(
      window,
      () => !byId("managedHistoryPanel").classList.contains("open"),
      "record panel closes after the final rejection",
    );
    byId("managedHistoryTrigger").click();
    await waitFor(
      window,
      () => byId("managedHistoryPanel").classList.contains("open"),
      "record panel reopens after the final rejection",
    );
    assert.equal(byId("managedInvitePending").hidden, true);
    assert.equal(byId("managedInvitePendingStatus").textContent, "");
    await waitFor(
      window,
      () => !byId("toasts").querySelector("[data-managed-invite-toast='1']"),
      "rejection toast disappears instead of becoming a record",
      2200,
    );

    api.publish([invite(secondId, "乙小机", "第二个问题")]);
    await waitFor(
      window,
      () => byId("managedInvitePendingList").children.length === 1,
      "a later invitation batch appears normally after the rejection feedback expires",
    );
    assert.equal(byId("managedInvitePending").hidden, false);
    assert.equal(byId("managedInvitePendingStatus").textContent, "");
    assert.match(byId("managedInvitePendingList").textContent, /乙小机/);
    assert.match(byId("managedInvitePendingList").textContent, /第二个问题/);

    api.state.current = {
      id: "current_session",
      phase: "returned",
      draws: [{position: 0, card_id: "M00", revealed: true}],
      reading: {state: "succeeded", text: "已保存旧解读"},
    };
    byId("ritualBar").classList.remove("hidden");
    byId("readingPanel").classList.add("open");
    const freshSecondRow = byId("managedInvitePendingList").children[0];
    freshSecondRow.querySelector(".managed-invite-accept").click();
    freshSecondRow.querySelector(".managed-invite-accept").click();
    await waitFor(
      window,
      () => requests.some(({url}) => url.endsWith(`/${secondId}/accept`)),
      "clean current state accepts through the existing invitation endpoint",
    );
    assert.equal(
      api.state.current.reading.state,
      "succeeded",
      "accepting from a restored terminal result does not mutate or rerun its reading",
    );
    const accepts = requests.filter(({url}) => url.endsWith(`/${secondId}/accept`));
    assert.equal(accepts.length, 1, "rapid accept clicks are de-duplicated");
    assert.equal(accepts[0].options.method, "POST");
    assert.equal(accepts[0].options.credentials, "same-origin");
    assert.equal(accepts[0].options.headers["X-Tarot-CSRF"], "csrf-BBBB");
    assert.equal(accepts[0].options.body, "{}");

    const waitRequests = requests.filter(({url}) => (
      new URL(url, "https://toy.example/").pathname === "/api/tarot/invitations/pending"
    ));
    assert.ok(waitRequests.some(({url}) => (
      new URL(url, "https://toy.example/").searchParams.get("wait_seconds") === "25"
    )), "game page uses bounded long-wait reception rather than a rapid poll");
  } finally {
    window.dispatchEvent(new window.Event("pagehide"));
  }
}

async function runIdentityAndResume() {
  const requests = [];
  let api;
  const dom = new JSDOM(pageHtml(), {
    runScripts: "outside-only",
    pretendToBeVisual: true,
    url: "https://toy.example/tarot/session/current_session/",
  });
  const {window} = dom;
  window.__hidden = false;
  Object.defineProperty(window.Document.prototype, "hidden", {
    configurable: true,
    get() { return window.__hidden; },
  });
  api = makeInvitationApi(window, requests);
  window.fetch = api.fetch;
  for (const source of uiScripts) window.eval(source);
  try {
    await waitFor(window, () => api.waiting() === 1, "identity harness starts waiting");
    window.__hidden = true;
    window.document.dispatchEvent(new window.Event("visibilitychange"));
    await waitFor(window, () => api.waiting() === 0, "hidden game page aborts its pending wait");
    api.publish([invite("C".repeat(32), "后台小机", "回前台后读取")]);
    window.__hidden = false;
    window.document.dispatchEvent(new window.Event("visibilitychange"));
    await waitFor(
      window,
      () => window.document.getElementById("managedInvitePendingList")?.children.length === 1,
      "foreground resume performs an immediate fresh invitation read",
    );

    api.state.ownerId = 102;
    api.publish([invite("D".repeat(32), "另一身份小机", "不应泄露到旧页面")]);
    await waitFor(
      window,
      () => /登录身份已变化/.test(
        window.document.getElementById("managedInvitePendingStatus")?.textContent || "",
      ),
      "a cookie identity switch invalidates the old page receiver",
    );
    assert.equal(window.document.getElementById("managedInvitePendingList").children.length, 0);
    assert.doesNotMatch(window.document.body.textContent, /不应泄露到旧页面/);
    await new Promise(resolve => window.setTimeout(resolve, 30));
    assert.equal(api.waiting(), 0, "identity-invalid page does not restart its receiver");
  } finally {
    window.dispatchEvent(new window.Event("pagehide"));
  }
}

Promise.resolve()
  .then(runGamePage)
  .then(runIdentityAndResume)
  .then(() => console.log("tarot homepage invitation absence + in-game receive/queue/actions/isolation (jsdom): ok"))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
