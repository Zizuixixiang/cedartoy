"use strict";

const assert = require("node:assert/strict");
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
const html = rendered.stdout;
const styleMatch = html.match(/<style>([\s\S]*?)<\/style>/);
assert.ok(styleMatch, "rendered homepage must retain its stylesheet");
csstree.parse(styleMatch[1]);

function response(body, ok = true, status = ok ? 200 : 400) {
  return {ok, status, json: async () => body};
}

function invitation(sessionId, machineName, question) {
  return {
    session_id: sessionId,
    machine_name: machineName,
    question,
    expires_at: 1_900_000_000,
    csrf_token: `csrf-${sessionId.slice(0, 4)}`,
  };
}

function createInvitationApi(window, requests) {
  const accounts = new Map([
    ["human-token-1", {
      user: {id: 101, username: "HumanOne", is_ai: false},
      bindings: [{id: 201, username: "BotOne", is_ai: true}],
      pending: [],
      version: 0,
    }],
    ["human-token-2", {
      user: {id: 102, username: "HumanTwo", is_ai: false},
      bindings: [{id: 202, username: "BotTwo", is_ai: true}],
      pending: [],
      version: 0,
    }],
  ]);
  const waiters = [];

  const bearer = (options = {}) => {
    const value = options.headers?.Authorization || "";
    return value.startsWith("Bearer ") ? value.slice(7) : "";
  };
  const cursor = (account) => account.version.toString(16).padStart(64, "0");
  const snapshot = (account) => ({
    invitations: account.pending.map((item) => ({...item})),
    cursor: cursor(account),
  });
  const removeWaiter = (waiter) => {
    const index = waiters.indexOf(waiter);
    if (index >= 0) waiters.splice(index, 1);
  };

  function publish(token, pending) {
    const account = accounts.get(token);
    assert.ok(account, `unknown test account ${token}`);
    account.pending = pending.map((item) => ({...item}));
    account.version += 1;
    for (const waiter of [...waiters]) {
      if (waiter.token !== token) continue;
      removeWaiter(waiter);
      waiter.resolve(response(snapshot(account)));
    }
  }

  async function fetch(url, options = {}) {
    requests.push({url, options});
    const parsed = new URL(url, "https://toy.example/");
    const token = bearer(options);
    const account = accounts.get(token);

    if (parsed.pathname === "/api/auth/me") {
      return account
        ? response({user: account.user, bindings: account.bindings})
        : response({error: "unauthorized"}, false, 401);
    }
    if (parsed.pathname === "/api/tarot/invitations/pending") {
      if (!account) return response({error: "unauthorized"}, false, 401);
      const afterCursor = parsed.searchParams.get("cursor");
      const waitSeconds = Number(parsed.searchParams.get("wait_seconds") || 0);
      if (!afterCursor || afterCursor !== cursor(account) || waitSeconds <= 0) {
        return response(snapshot(account));
      }
      return new Promise((resolve, reject) => {
        const waiter = {token, resolve, reject};
        waiters.push(waiter);
        if (options.signal) {
          options.signal.addEventListener("abort", () => {
            removeWaiter(waiter);
            reject(new window.DOMException("Aborted", "AbortError"));
          }, {once: true});
        }
      });
    }
    if (parsed.pathname === "/api/tarot/browser-login") {
      return account
        ? response({ok: true, user_id: account.user.id})
        : response({error: "unauthorized"}, false, 401);
    }
    const action = parsed.pathname.match(
      /^\/api\/tarot\/invitations\/([A-Za-z0-9_-]{32,128})\/(accept|reject)$/,
    );
    if (action) {
      if (!account) return response({error: "unauthorized"}, false, 401);
      const sessionId = action[1];
      const answer = action[2];
      if (!account.pending.some((item) => item.session_id === sessionId)) {
        return response({error: "already processed"}, false, 409);
      }
      publish(token, account.pending.filter((item) => item.session_id !== sessionId));
      return response({
        phase: answer === "accept" ? "accepted" : "stopped",
        invitation_state: answer === "accept" ? "accepted" : "rejected",
      });
    }
    if (parsed.pathname === "/api/announcements") {
      return response({announcements: [], unread_count: 0, authenticated: Boolean(account)});
    }
    if (parsed.pathname === "/api/auth/avatar") return response({supported: true, type: "emoji"});
    if (parsed.pathname === "/soup/api/rooms/") return response({count: 0});
    if (parsed.pathname === "/api/platform-stats") return response({});
    if (parsed.pathname === "/api/games/stats") return response({});
    return response({}, false, 404);
  }

  return {
    fetch,
    publish,
    waiting(token) {
      return waiters.filter((waiter) => waiter.token === token).length;
    },
  };
}

async function waitFor(window, predicate, message, timeoutMs = 1500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => window.setTimeout(resolve, 10));
  }
  assert.fail(message);
}

async function runPage() {
  const requests = [];
  let api;
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (error) => {
    if (!/Not implemented: navigation/.test(error.message)) throw error;
  });
  const dom = new JSDOM(html, {
    url: "https://toy.example/",
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse(window) {
      window.__testHidden = false;
      Object.defineProperty(window.Document.prototype, "hidden", {
        configurable: true,
        get() { return window.__testHidden; },
      });
      Object.defineProperty(window.Document.prototype, "visibilityState", {
        configurable: true,
        get() { return window.__testHidden ? "hidden" : "visible"; },
      });
      window.localStorage.setItem("cedartoy_token", "human-token-1");
      api = createInvitationApi(window, requests);
      window.fetch = api.fetch;
    },
  });

  try {
    const {window} = dom;
    const byId = (id) => window.document.getElementById(id);
    const firstId = "S".repeat(32);
    const backgroundId = "B".repeat(32);
    const secondAccountId = "T".repeat(32);

    await waitFor(
      window,
      () => api.waiting("human-token-1") === 1,
      "visible signed-in homepage must establish one invitation wait",
    );
    assert.equal(byId("tarotInviteModal").classList.contains("show"), false);

    window.openPlaceholder("正在处理别的操作", "塔罗邀请不得覆盖此弹窗");
    api.publish("human-token-1", [
      invitation(
        firstId,
        "<b>测试小机</b>",
        "<img src=x onerror=alert(1)>\n我该如何选择？",
      ),
    ]);
    await waitFor(
      window,
      () => byId("notificationBadge").textContent === "1",
      "a later invite must update the existing notification bell without refresh",
    );
    assert.equal(byId("placeholderModal").classList.contains("show"), true);
    assert.equal(byId("tarotInviteModal").classList.contains("show"), false);

    window.closeModals();
    await waitFor(
      window,
      () => byId("tarotInviteModal").classList.contains("show"),
      "queued invite must appear after the existing modal closes",
    );
    assert.equal(byId("tarotInviteMachine").textContent, "<b>测试小机</b> 想问：");
    assert.equal(
      byId("tarotInviteQuestion").textContent,
      "<img src=x onerror=alert(1)>\n我该如何选择？",
    );
    assert.equal(byId("tarotInviteQuestion").querySelector("img"), null);

    window.closeModals();
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    assert.equal(byId("tarotInviteModal").classList.contains("show"), false);
    assert.equal(byId("notificationBadge").hidden, false);
    byId("notificationBell").click();
    await waitFor(
      window,
      () => byId("tarotInviteModal").classList.contains("show"),
      "the existing notification bell must reopen an unresolved invite",
    );

    byId("tarotInviteReject").click();
    await waitFor(
      window,
      () => !byId("tarotInviteModal").classList.contains("show")
        && byId("notificationBadge").hidden,
      "reject must remove the processed invite exactly once",
    );
    const rejectRequest = requests.find(({url}) => url.endsWith(`/${firstId}/reject`));
    assert.ok(rejectRequest);
    assert.equal(rejectRequest.options.method, "POST");
    assert.equal(rejectRequest.options.headers.Authorization, "Bearer human-token-1");
    assert.equal(rejectRequest.options.headers["X-Tarot-CSRF"], "csrf-SSSS");
    assert.equal(rejectRequest.options.body, "{}");
    api.publish("human-token-1", []);
    await new Promise((resolve) => window.setTimeout(resolve, 30));
    window.dispatchEvent(new window.Event("focus"));
    await new Promise((resolve) => window.setTimeout(resolve, 30));
    assert.equal(
      byId("tarotInviteModal").classList.contains("show"),
      false,
      "processed invitations must not be prompted again",
    );

    window.__testHidden = true;
    window.document.dispatchEvent(new window.Event("visibilitychange"));
    await waitFor(
      window,
      () => api.waiting("human-token-1") === 0,
      "hidden page must abort its invitation wait",
    );
    api.publish("human-token-1", [
      invitation(backgroundId, "后台小机", "回到前台后看见我"),
    ]);
    await new Promise((resolve) => window.setTimeout(resolve, 30));
    assert.equal(byId("tarotInviteModal").classList.contains("show"), false);
    window.__testHidden = false;
    window.document.dispatchEvent(new window.Event("visibilitychange"));
    await waitFor(
      window,
      () => byId("tarotInviteModal").classList.contains("show")
        && byId("tarotInviteQuestion").textContent === "回到前台后看见我",
      "foreground recovery must immediately reload this account's pending invites",
    );

    window.localStorage.setItem("cedartoy_token", "human-token-2");
    await window.loadMe();
    await waitFor(
      window,
      () => api.waiting("human-token-2") === 1,
      "account switch must establish a wait for the new account",
    );
    assert.equal(byId("tarotInviteModal").classList.contains("show"), false);
    assert.equal(byId("notificationBadge").hidden, true);
    api.publish("human-token-1", [
      invitation(backgroundId, "后台小机", "这是旧账号的问题"),
    ]);
    await new Promise((resolve) => window.setTimeout(resolve, 30));
    assert.equal(byId("tarotInviteModal").classList.contains("show"), false);

    api.publish("human-token-2", [
      invitation(secondAccountId, "二号小机", "只属于二号账号的问题"),
    ]);
    await waitFor(
      window,
      () => byId("tarotInviteModal").classList.contains("show")
        && byId("tarotInviteQuestion").textContent === "只属于二号账号的问题",
      "new account must receive only its own later invite",
    );
    byId("tarotInviteAccept").click();
    await waitFor(
      window,
      () => requests.some(({url}) => url.endsWith(`/${secondAccountId}/accept`)),
      "accept must use the existing human-controlled response path",
    );
    const browserLogin = requests.find(({url, options}) => (
      url === "/api/tarot/browser-login"
        && options.headers?.Authorization === "Bearer human-token-2"
    ));
    const acceptRequest = requests.find(({url}) => url.endsWith(`/${secondAccountId}/accept`));
    assert.ok(browserLogin, "acceptance must establish the existing Tarot cookie flow");
    assert.ok(requests.indexOf(browserLogin) < requests.indexOf(acceptRequest));
    assert.equal(acceptRequest.options.headers["X-Tarot-CSRF"], "csrf-TTTT");

    const pendingRequests = requests.filter(({url}) => (
      new URL(url, "https://toy.example/").pathname === "/api/tarot/invitations/pending"
    ));
    assert.ok(
      pendingRequests.some(({url}) => new URL(url, "https://toy.example/").searchParams.get("wait_seconds") === "25"),
      "homepage must use a bounded per-account invitation wait",
    );
    assert.ok(pendingRequests.length < 15, "invitation checks must not become a high-frequency loop");
  } finally {
    dom.window.close();
  }
}

runPage()
  .then(() => console.log("tarot homepage invitation receive/resume/queue/isolation (jsdom): ok"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
