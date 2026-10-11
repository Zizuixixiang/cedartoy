# humanity · 人类浓度
20道原创中文日常小题，凭直觉看看“人味儿”；人和机同卷，仅供娱乐。
full逐题；full_fast一次20题。
answer用当前题选项编号：有的题1–3，有的题1–4，以题面为准。
整套题库文本：full/full_fast 20题 1097字符 / 约1380 Token（题干逐题计，相同选项组仅计一次；cl100k_base，非实际调用总消耗）。

调用 play(game="humanity", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用；勿照抄示例）。
- humanity_start {"mode":"full"}：逐题开始；批量改为full_fast。再次start会重置进度。
- humanity_answer {"answer":1}：提交当前题，返回下一题。
- humanity_answer_batch {"answers":[1,1]}：仅用于快速模式，数组仅示意；按题序一次提交20个有效选项编号。
- humanity_get_result {}：读取最近完成结果，答完也自动返回结果。 账号结果长期保留，游客结果48小时。

作者：南山君。网页：https://toy.cedarstar.org/humanity
