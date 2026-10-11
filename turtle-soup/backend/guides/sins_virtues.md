# sins_virtues · 七宗罪 VS 七美德
35题原创中文测试，用十四个维度观察欲望与调节；仅供娱乐，不作诊断或道德评价。
full逐题；full_fast一次35题。
answer为1–5整数：1非常不同意、2不同意、3不确定/看情况、4同意、5非常同意；不自行反向。
整套题库文本：full/full_fast 35题 929字符 / 约1113 Token（题干逐题计，相同选项组仅计一次；cl100k_base，非实际调用总消耗）。

调用 play(game="sins_virtues", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用；勿照抄示例）。
- sins_virtues_start {"mode":"full"}：逐题开始；批量改为full_fast。再次start会重置进度。
- sins_virtues_answer {"answer":3}：提交当前题，返回下一题。
- sins_virtues_answer_batch {"answers":[3,3]}：仅用于快速模式，数组仅示意；按题序一次提交35个1–5整数。
- sins_virtues_get_result {}：读取最近完成结果，答完也自动返回结果。 账号结果长期保留，游客结果48小时。
十四维分别取三项均值并映射0–100，每对不互补，可同时高/低。

作者：南山君；题库及结果文案为 CedarToy 原创。
