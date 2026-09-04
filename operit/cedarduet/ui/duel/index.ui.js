function Screen(ctx) {
  const [username, setUsername] = ctx.useState("username", "");
  const [password, setPassword] = ctx.useState("password", "");
  const [authState, setAuthState] = ctx.useState("authState", "checking");
  const [accountName, setAccountName] = ctx.useState("accountName", "");
  const [loadingAction, setLoadingAction] = ctx.useState("loadingAction", "");
  const [webUrl, setWebUrl] = ctx.useState("webUrl", "");
  const [error, setError] = ctx.useState("error", "");
  const [notice, setNotice] = ctx.useState("notice", "");
  const authLoadStarted = ctx.useRef("authLoadStarted", false);

  function ordinaryMessage(result, fallback) {
    const raw = result && result.message ? String(result.message) : "";
    if (/登录已失效/.test(raw)) return "登录已失效，请重新登录";
    if (/(token|ticket|票据|confirm|bearer|jwt)/i.test(raw)) return fallback;
    return raw || fallback;
  }

  function validationError() {
    const name = username.trim();
    if (!name || !password) return "请输入用户名和密码";
    if (name.length < 2 || name.length > 20) return "用户名长度须为 2-20 个字符";
    if (!/^[a-zA-Z0-9_\u4e00-\u9fff]+$/.test(name)) {
      return "用户名只能包含字母、数字、下划线和中文";
    }
    if (password.length < 6) return "密码至少 6 位";
    return "";
  }

  async function restoreLogin() {
    if (authLoadStarted.current) return;
    authLoadStarted.current = true;
    setError("");
    try {
      const result = await ctx.callTool("cedarduet:human_session_status", {});
      if (result && result.success === true && result.data && result.data.logged_in === true) {
        setAccountName(String(result.data.user && result.data.user.username || ""));
        setAuthState("logged_in");
        return;
      }
      setAccountName("");
      setAuthState("logged_out");
      if (result && result.data && result.data.expired === true) {
        setError("登录已失效，请重新登录");
      } else if (!result || result.success !== true) {
        setError(ordinaryMessage(result, "暂时无法检查登录状态，请稍后重试"));
      }
    } catch (cause) {
      setAccountName("");
      setAuthState("logged_out");
      setError("暂时无法检查登录状态，请稍后重试");
      if (ctx.reportError) ctx.reportError(cause);
    }
  }

  async function submitAccount(action) {
    if (loadingAction) return;
    const invalid = validationError();
    if (invalid) {
      setError(invalid);
      return;
    }
    const registering = action === "register";
    setLoadingAction(action);
    setError("");
    setNotice("");
    try {
      const result = await ctx.callTool(
        registering ? "cedarduet:human_register" : "cedarduet:human_login",
        { username: username.trim(), password: password }
      );
      if (!result || result.success !== true || !result.data || result.data.logged_in !== true) {
        throw new Error(ordinaryMessage(
          result,
          registering ? "注册失败，请检查填写内容" : "登录失败，请检查用户名和密码"
        ));
      }
      const signedInName = String(result.data.user && result.data.user.username || username.trim());
      setAccountName(signedInName);
      setUsername("");
      setAuthState("logged_in");
      setNotice(ordinaryMessage(result, registering ? "注册并登录成功" : "登录成功"));
    } catch (cause) {
      const fallback = registering
        ? "注册失败，请检查填写内容"
        : "登录失败，请检查用户名和密码";
      setError(ordinaryMessage(cause, fallback));
      if (ctx.reportError) ctx.reportError(cause);
    } finally {
      // The password only lives in UI memory for the active request.
      setPassword("");
      setLoadingAction("");
    }
  }

  async function logout() {
    if (loadingAction) return;
    setLoadingAction("logout");
    setError("");
    setNotice("");
    try {
      const result = await ctx.callTool("cedarduet:human_logout", {});
      if (!result || result.success !== true) {
        throw new Error(ordinaryMessage(result, "退出失败，请稍后重试"));
      }
      setAccountName("");
      setUsername("");
      setPassword("");
      setAuthState("logged_out");
    } catch (cause) {
      setError(ordinaryMessage(cause, "退出失败，请稍后重试"));
      if (ctx.reportError) ctx.reportError(cause);
    } finally {
      setLoadingAction("");
    }
  }

  async function openDuel() {
    if (loadingAction) return;
    setLoadingAction("enter");
    setError("");
    setNotice("");
    try {
      // Clicking the button is the explicit confirmation for this entry. The
      // package uses the saved session and sends the required confirmation.
      const result = await ctx.callTool("cedarduet:human_duel_entry", {});
      if (!result || result.success !== true || !result.data || !result.data.web_url) {
        throw new Error(ordinaryMessage(result, "暂时无法进入双弈，请稍后重试"));
      }
      setWebUrl(String(result.data.web_url));
    } catch (cause) {
      const message = ordinaryMessage(cause, "暂时无法进入双弈，请稍后重试");
      setError(message);
      if (/登录已失效/.test(message)) {
        setAccountName("");
        setAuthState("logged_out");
      }
      if (ctx.reportError) ctx.reportError(cause);
    } finally {
      setLoadingAction("");
    }
  }

  if (webUrl) {
    const originMatch = String(webUrl).match(/^(https?:\/\/[^/]+)/i);
    const allowedOrigin = originMatch ? originMatch[1].toLowerCase() : "";
    return ctx.UI.WebView({
      url: webUrl,
      fillMaxSize: true,
      javaScriptEnabled: true,
      domStorageEnabled: true,
      databaseEnabled: false,
      allowFileAccess: false,
      allowContentAccess: false,
      allowFileAccessFromFileURLs: false,
      allowUniversalAccessFromFileURLs: false,
      mixedContentMode: "neverAllow",
      safeBrowsingEnabled: true,
      acceptThirdPartyCookies: false,
      onShouldOverrideUrlLoading: function (request) {
        const nextUrl = String((request && request.url) || "");
        const normalized = nextUrl.toLowerCase();
        if (
          allowedOrigin
          && (normalized === allowedOrigin || normalized.indexOf(allowedOrigin + "/") === 0)
        ) {
          return { action: "allow" };
        }
        return { action: "external", url: nextUrl };
      },
    });
  }

  if (authState === "checking") {
    return ctx.UI.LazyColumn({
      padding: { horizontal: 18, vertical: 20 },
      spacing: 14,
      fillMaxSize: true,
      onLoad: restoreLogin,
    }, [
      ctx.UI.Text({ text: "CedarDuet 双弈", style: "headlineSmall" }),
      ctx.UI.Text({ text: "正在检查登录状态…", style: "bodyMedium" }),
    ]);
  }

  if (authState === "logged_in") {
    return ctx.UI.LazyColumn({
      padding: { horizontal: 18, vertical: 20 },
      spacing: 14,
      fillMaxSize: true,
    }, [
      ctx.UI.Text({ text: "CedarDuet 双弈", style: "headlineSmall" }),
      ctx.UI.Text({ text: "当前账号：" + accountName, style: "titleMedium" }),
      ctx.UI.Text({ text: "点击进入后会安全打开双弈网页。", style: "bodyMedium" }),
      notice ? ctx.UI.Text({ text: notice, color: "#2E7D32", style: "bodySmall" }) : null,
      error ? ctx.UI.Text({ text: error, color: "#B3261E", style: "bodyMedium" }) : null,
      ctx.UI.Button({
        text: loadingAction === "enter" ? "正在进入…" : "进入双弈",
        enabled: !loadingAction,
        onClick: openDuel,
        fillMaxWidth: true,
      }),
      ctx.UI.Button({
        text: loadingAction === "logout" ? "正在退出…" : "退出登录",
        enabled: !loadingAction,
        onClick: logout,
        fillMaxWidth: true,
      }),
    ]);
  }

  return ctx.UI.LazyColumn({
    padding: { horizontal: 18, vertical: 20 },
    spacing: 14,
    fillMaxSize: true,
  }, [
    ctx.UI.Text({ text: "CedarDuet 双弈", style: "headlineSmall" }),
    ctx.UI.Text({
      text: "使用 CedarToy 人类账号登录；没有账号可以直接注册。",
      style: "bodyMedium",
    }),
    ctx.UI.TextField({
      value: username,
      onValueChange: setUsername,
      label: "用户名",
      placeholder: "2-20 位字母、数字、下划线或中文",
      singleLine: true,
      fillMaxWidth: true,
    }),
    ctx.UI.TextField({
      value: password,
      onValueChange: setPassword,
      label: "密码",
      placeholder: "至少 6 位",
      isPassword: true,
      singleLine: true,
      fillMaxWidth: true,
    }),
    error ? ctx.UI.Text({ text: error, color: "#B3261E", style: "bodyMedium" }) : null,
    ctx.UI.Button({
      text: loadingAction === "login" ? "正在登录…" : "登录",
      enabled: !loadingAction,
      onClick: function () { return submitAccount("login"); },
      fillMaxWidth: true,
    }),
    ctx.UI.Button({
      text: loadingAction === "register" ? "正在注册…" : "注册",
      enabled: !loadingAction,
      onClick: function () { return submitAccount("register"); },
      fillMaxWidth: true,
    }),
  ]);
}

exports.default = Screen;
