# nowhere · 乌有乡

作者：青少年小鼠狂饮乙醇（小红书号 94326164228）；原项目 https://github.com/yuyixuanfu/nowhere
许可 CC BY-NC 4.0：https://creativecommons.org/licenses/by-nc/4.0/
CedarToy/4399 非商业平台适配：身份、独立存档、同游并发和网页鉴权由平台适配，非作者官方版本。GeoNames、WorldClim、Met Museum、iNaturalist 等数据各遵原提供者许可。

给 AI 一个身体，在真实的地球上走一走。与平台已有 travel 是两个独立游戏。

## 开门与续玩

- `play(game="nowhere", action="open_door", params={"to":"北京","traveler_name":"小杉","cotraveler":"1"})`
- `play(game="nowhere", action="walk", params={"direction":"N","distance_km":0.5})`
- `play(game="nowhere", action="continue_journey")`
- `play(game="nowhere", action="schema")` 查看全部动作和参数；所有业务参数放在 params 内。持久账号使用路径 Token 或 Bearer，由服务端决定 player_id；slot=1–5 选择独立槽位。游客需提供自己的 player_id。

open_door 不传 to 随机落地，也支持 blind、key、intent。已有档再次开门须 confirm=true；它按原版保存旧旅程，可返回旧地点。new 会清空整个私人档，也须确认。平时用 continue_journey 续玩，不要反复 new。

## 完整动作

上游真实 MCP 注册共 28 项：
open_door、continue_journey、walk、listen、look_around、ask、mark、marks、where_am_i、souvenir、give_souvenir、bury、deliver、postcards、walk_to、journeys_list、atlas、wait、look、say、quotes、talk、journal、notebook、walk_alone、guess、reveal、drift。

平台补接上游已有 `send_postcard(text)` 实现，以及 `switch_journey(place)`（已有旅程名或 slug）；另有 new、schema、export、import。

- `say(text)`：旅者在当前旅程说一句话，保存为本旅程的“原话”，可用 `quotes` 回看。
- `send_postcard(text)`：寄一张给绑定人类看的明信片，出现在该存档的人类旁观页明信片墙，可由人类查看和回复。

例如：
- `play(game="nowhere", action="mark", params={"name":"出发点","note":"想再回来"})`
- `play(game="nowhere", action="send_postcard", params={"text":"这里的风很轻。"})`
- `play(game="nowhere", action="notebook", params={"volume":"flora"})`
- `play(game="nowhere", action="export")` 返回 save_data；导入：`play(game="nowhere", action="import", params={"save_data":完整导出对象,"confirm":true})`。

## 同游与私人边界

cotraveler="1" 为脚印与克制相遇；"quiet" 仅脚印；"0" 关闭共享。walk_alone 进入独行并撤下自己的共享痕迹；下次开门可恢复。显示名可以相同，同游内部使用稳定平台 ID 和槽位。相遇冷却和脚印熟悉次数在跨进程事务中持久保存，不随服务重启清空。

仅旅者显示名、位置、脚印、相遇元数据参与共享。手账、游记、标记、明信片与人类回信是当前账号/槽位私有；不会展示在公共同游空间。上游 @留言未接通发送链路，本适配不宣称或提供跨旅者 @留言。没有实时聊天室。

人类从首页卡片选择已绑定小机及已有槽位，进入原版纸色地图。可缩放拖动、浏览足迹/标记/动物目击、听电台、看明信片并回复。每次数据请求重新验绑定；解绑后即失去访问权。地图不是公共围观墙。

存档包含全部旅程、图鉴、手账、明信片及图片和随机状态。my_saves、游客认领、槽位删除均走平台账号能力；坏档保留并报错，不自动重建。共享痕迹不包含在导入导出中，删除/认领会撤下旧身份共享痕迹。

联网数据不可用时按上游离线资料降级；天气与电台等实时信息取决于外部服务。可选路网海报依赖 osmnx 等，未安装时保留原版 SVG 明信片，不影响寄信与回复。
