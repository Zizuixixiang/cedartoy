# dnd · 阵营九宫格
了解守序/混乱与善/恶的阵营倾向。
full：36题逐题；full_fast：一次36题。
answer为1–4整数，对应当前题四个选项；MCP为英文原文。
整套题库文本：full/full_fast 36题 8808字符 / 约2077 Token（题干逐题计，相同选项组仅计一次；cl100k_base，非实际调用总消耗）。

调用 play(game="dnd", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用；勿照抄示例）。
- dnd_start {"mode":"full"}：逐题开始；批量改为full_fast。再次start会重置进度。
- dnd_answer {"answer":1}：提交当前题，返回下一题。
- dnd_answer_batch {"answers":[1,1]}：仅用于快速模式，数组仅示意；按题序一次提交36个1–4整数。
- dnd_get_result {}：读取最近完成结果，答完也自动返回结果。 账号结果长期保留，游客结果48小时。

作者/适配：南山君；题库与阵营描述来源 easydamus.com（https://easydamus.com/alignmenttest.html）；网页题目及结果描述有中文翻译。
