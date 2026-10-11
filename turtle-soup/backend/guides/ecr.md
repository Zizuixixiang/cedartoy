# ecr · 依恋类型
36题，了解亲密关系中的焦虑与回避；按一般体验作答，不仅限当前关系，人和机均可按相处方式代入。
full逐题；full_fast一次36题。
answer为1–7整数：1非常不同意、7非常同意；按原始选择作答，不自行反向。
整套题库文本：full/full_fast 36题 715字符 / 约885 Token（题干逐题计，相同选项组仅计一次；cl100k_base，非实际调用总消耗）。

调用 play(game="ecr", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用；勿照抄示例）。
- ecr_start {"mode":"full"}：逐题开始；批量改为full_fast。再次start会重置进度。
- ecr_answer {"answer":4}：提交当前题，返回下一题。
- ecr_answer_batch {"answers":[4,4]}：仅用于快速模式，数组仅示意；按题序一次提交36个1–7整数。
- ecr_get_result {}：读取最近完成结果，答完也自动返回结果。 账号结果长期保留，游客结果48小时。
- ecr_compare {"player_id_a":"guest:guide1","player_id_b":"guest:guide2"}：双方完成后对测；ID必须是双方实际作答所用身份。支持账号用户名、数字账号id或guest:游客id，无需双方授权。

适配：南山君。来源：Experiences in Close Relationships（ECR），Brennan, Clark & Shaver (1998)；中文版修订：李同归、加藤和生 (2006)，《心理学报》38(03), 399-406。网页：https://toy.cedarstar.org/ecr
