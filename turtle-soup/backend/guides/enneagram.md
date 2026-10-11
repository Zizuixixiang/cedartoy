# enneagram · 九型人格
了解主型与脑/心/腹三中心。
quick/quick_fast：36题A/B；full/full_fast：180题频率量表。quick、full逐题；quick_fast一次36题，full_fast每批最多16题（末批4题）。
quick：1=A、2=B；full：1=Almost Never、2=Rarely、3=Sometimes、4=Often、5=Almost Always。MCP题目为英文原文。
整套题库文本：quick/quick_fast 36题 5860字符 / 约1229 Token；full/full_fast 180题 10916字符 / 约2320 Token（题干逐题计，相同选项组仅计一次；cl100k_base，非实际调用总消耗）。

调用 play(game="enneagram", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用；勿照抄示例）。
- enneagram_start {"mode":"quick"}：逐题开始；批量改为quick_fast。再次start会重置进度。
- enneagram_answer {"answer":1}：提交当前题，返回下一题。
- enneagram_answer_batch {"answers":[1,1]}：仅用于快速模式，数组仅示意；按本批题序提交，长度须等于本批题数；返回下一批，直到完成。
- enneagram_get_result {}：读取最近完成结果，答完也自动返回结果。 账号结果长期保留，游客结果48小时。
quick只报主型和三中心36分制相对分；侧翼与tritype仅full档提供，两档分数不可直接比较。结果默认精简版；完整文案用enneagram_get_result {"detail":"full"}。

作者：Max Ross；https://github.com/kcdjmaxx/enneagram-llm-evaluator ，MIT。
