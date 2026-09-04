"use strict";

const DUEL_ROUTE = "toolpkg:org.cedarstar.cedarduet:ui:duel";

function registerToolPkg() {
  ToolPkg.registerUiRoute({
    id: "duel",
    route: DUEL_ROUTE,
    runtime: "compose_dsl",
    screen: "ui/duel/index.ui.js",
    title: { zh: "双弈", en: "CedarDuet" },
  });
  ToolPkg.registerNavigationEntry({
    id: "duel_sidebar",
    route: DUEL_ROUTE,
    surface: "main_sidebar_plugins",
    title: { zh: "双弈", en: "CedarDuet" },
    icon: "SportsEsports",
    order: 40,
  });
  return true;
}

exports.registerToolPkg = registerToolPkg;
