# 瓶中生态 说明
文字生态模拟：从空池塘开始，投放物种、推进时间、观察生态自行演化。没有积分，没有通关。

## MCP 工具

常用指令：
- eco_new — 开新局。参数：player_id（1-10位字母数字）、seed（可选整数）
- eco_observe — 观察池塘。参数：action（observe 推进一天 / wait 连续推进 / gaze 凝望不推进 / look 查看详情）、days（wait天数1-7）、target（look的物种或季节名）
- eco_act — 干预池塘。参数：action（summon 投放 / remove 取走 / feed 投喂 / clean 换水 / crack 凿冰·冬季 / shelter 铺落叶·冬季 / choose 做选择 / name 给定居者取名）、species（物种名）、quantity（数量，默认10/10/1）、option（1或2，choose用）、settler（物种名或[D-N]编号，name用）、nickname（昵称，name用）
- eco_info — 查看信息。参数：action（status 数据面板 / folio 万物志 / chronicle 年鉴 / encyclopedia 图鉴与成就 / trends 趋势图）、scope（chronicle范围 recent/all）
- eco_save — 存档管理。参数：action（export / import）、mode（export模式 full/lite/story）、import 需 save_data（base64串）

## 批量与省token

eco_observe 的 wait 可一次推多天（days=1-7），eco_act 的 summon/remove 支持 quantity 批量操作。
完整文档见 toy.cedarstar.org

原作信息：南山君 & 🤖Clio（小红书号 501518888）／eco 引擎
