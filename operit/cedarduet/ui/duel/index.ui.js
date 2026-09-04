function Screen(ctx) {
  const [humanToken, setHumanToken] = ctx.useState("humanToken", "");
  const [confirmed, setConfirmed] = ctx.useState("confirmed", false);
  const [loading, setLoading] = ctx.useState("loading", false);
  const [webUrl, setWebUrl] = ctx.useState("webUrl", "");
  const [error, setError] = ctx.useState("error", "");

  async function openDuel() {
    if (!humanToken.trim() || confirmed !== true) {
      setError("请粘贴已登录人类 Token，并勾选本次网页登录确认。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const result = await ctx.callTool("cedarduet:duel_web_ticket", {
        human_token: humanToken.trim(),
        confirm: true,
      });
      if (!result || result.success !== true || !result.data || !result.data.web_url) {
        throw new Error((result && result.message) || "未取得网页登录票据");
      }
      // The long-lived human token is never put in the URL and is dropped from UI state.
      setHumanToken("");
      setConfirmed(false);
      setWebUrl(result.data.web_url);
    } catch (cause) {
      const message = cause && cause.message ? cause.message : String(cause);
      setError(message);
      if (ctx.reportError) ctx.reportError(cause);
    } finally {
      setLoading(false);
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

  return ctx.UI.Column({ padding: 20, spacing: 14, fillMaxSize: true }, [
    ctx.UI.Text({ text: "CedarDuet 双弈", style: "headlineSmall" }),
    ctx.UI.Text({
      text: "网页登录需要一次明确确认。人类 Token 只用于换取 60 秒单次票据，不会放入 WebView URL。",
      style: "bodyMedium",
    }),
    ctx.UI.TextField({
      value: humanToken,
      onValueChange: setHumanToken,
      label: "人类登录 Token",
      placeholder: "从 CedarToy 已登录账号复制",
      isPassword: true,
      singleLine: true,
      fillMaxWidth: true,
    }),
    ctx.UI.Row({ spacing: 8, verticalAlignment: "center" }, [
      ctx.UI.Checkbox({
        checked: confirmed,
        onCheckedChange: setConfirmed,
        enabled: !loading,
      }),
      ctx.UI.Text({ text: "我确认本次进入双弈网页", style: "bodyMedium" }),
    ]),
    error ? ctx.UI.Text({ text: error, color: "#B3261E" }) : null,
    ctx.UI.Button({
      text: loading ? "正在创建单次票据…" : "进入双弈",
      enabled: !loading && confirmed && humanToken.trim().length > 0,
      onClick: openDuel,
      fillMaxWidth: true,
    }),
  ]);
}

exports.default = Screen;
