# bdsmtest · BDSMTest
了解性癖倾向，调用 bdsmtest.org 官方接口算分。通常93题，题数/题号以start返回为准；题目与原型名称使用原站中文（lang=zh），非本地翻译。
normal逐题；fast一次返回全题。score为1–7整数：1完全不同意、4中立、7完全同意，不接受0。
整套题库文本：原站实时出题，题干与选项 Token 总量未测；不能由通常93题推算。

调用 play(game="bdsmtest", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用；勿照抄示例）。
- bdsmtest_start {"mode":"normal"}：逐题开始；批量改为fast。再次start会重置进度。
- bdsmtest_answer {"score":4}：提交当前题，返回下一题。
- bdsmtest_answer_batch {"answers":{"3":7,"98":1,"2":4}}：仅fast；对象仅示意，键必须替换为start返回的全部题号id，每题1–7，不是数组。
- bdsmtest_get_result {}：读取最近完成结果，答完也自动算分；各原型百分比降序，并附 https://bdsmtest.org/r/{rid} 原站结果链接。 账号结果长期保留，游客结果48小时。

来源：https://bdsmtest.org ，CedarToy/南山君适配为MCP。玩法灵感来自圈内同好分享，最初分享者未能确认，欢迎原作者联系署名。
