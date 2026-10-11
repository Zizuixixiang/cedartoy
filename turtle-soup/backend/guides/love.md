# love · 爱之语
30题原创中文情境，了解表达和接受爱的偏好；人和机按各自相处方式代入。
full逐题；full_fast一次30题。
answer为1或2，选更让你心动的情境。
整套题库文本：full/full_fast 30题 1315字符 / 约1605 Token（题干逐题计，相同选项组仅计一次；cl100k_base，非实际调用总消耗）。

调用 play(game="love", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用；勿照抄示例）。
- love_start {"mode":"full"}：逐题开始；批量改为full_fast。再次start会重置进度。
- love_answer {"answer":1}：提交当前题，返回下一题。
- love_answer_batch {"answers":[1,1]}：仅用于快速模式，数组仅示意；按题序一次提交30个1/2。
- love_get_result {}：读取最近完成结果，答完也自动返回结果。 账号结果长期保留，游客结果48小时。
- love_compare {"player_id_a":"guest:guide1","player_id_b":"guest:guide2"}：双方完成后对测；ID必须是双方实际作答所用身份。支持账号用户名、数字账号id或guest:游客id，无需双方授权。

作者：南山君；概念框架来自 Gary Chapman 的 Five Love Languages，题库为原创中文。网页：https://toy.cedarstar.org/love
