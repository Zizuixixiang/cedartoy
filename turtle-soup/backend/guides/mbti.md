# mbti · MBTI 人格类型
用四维偏好了解人格类型。
short/short_fast：16题；full/full_fast：93题。short、full逐题；short_fast一次16题，full_fast每批最多16题（末批13题）。
a_score为0–5整数：5完全偏A、0完全偏B，中间值表示倾向强弱。
整套题库文本：short/short_fast 16题 492字符 / 约585 Token；full/full_fast 93题 1950字符 / 约2339 Token（题干逐题计，相同选项组仅计一次；cl100k_base，非实际调用总消耗）。

调用 play(game="mbti", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用；勿照抄示例）。
- mbti_start {"mode":"short"}：逐题开始；批量改为short_fast。再次start会重置进度。
- mbti_answer {"a_score":3}：提交当前题，返回下一题。
- mbti_answer_batch {"a_scores":[3,3]}：仅用于快速模式，数组仅示意；按本批题序提交，长度须等于本批题数；返回下一批，直到完成。
- mbti_get_result {}：读取最近完成结果，答完也自动返回结果。 账号结果长期保留，游客结果48小时。

作者：南山君；中文题库整理自网络公开题目。
