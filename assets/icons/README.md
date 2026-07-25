# 游戏卡片图标

放这里的图标会自动替换 index 首页游戏卡片上的字符图标（glyph）。
**没有对应图片的游戏，继续显示原来的字符，不受影响**，所以可以有几个放几个，不必凑齐。

## 命名规则

文件名 = 游戏 id + `.png`，全小写，例如 `delve.png`。

## 格式建议

- PNG，**透明背景**（卡片底色会透出来，白底会显得像贴纸）
- 建议 256×256 或 512×512。卡片实际显示约 48px、详情页约 82px，留 2–3 倍给高分屏
- 像素风素材请用**整数倍**放大导出（最近邻 / nearest-neighbor），不要用双线性插值，否则边缘会糊
- 单个控制在 100KB 以内

## id 对照表

| 文件名 | 游戏 |
|---|---|
| `soup.png` | 海龟汤 |
| `fishing.png` | AI钓鱼 |
| `eco.png` | 瓶中生态 |
| `ciyuwu.png` | 词与物 |
| `imitator_td.png` | 植物大战丧尸随机版 |
| `memoria.png` | Memoria Station |
| `arcade.png` | 街机厅 |
| `burger.png` | 午间汉堡铺 |
| `market.png` | 出门买菜上桌吃饭 |
| `leek.png` | 韭菜修炼之道 |
| `delve.png` | 下矿 |
| `travel.png` | 旅行 |
| `workkk.png` | AI打工人 |
| `garden_cat.png` | 花园与猫咪 |
| `moonlit.png` | 月幕万象 |
| `mbti.png` | MBTI 人格 |
| `dnd.png` | 九阵营 |
| `love.png` | 爱之语 |
| `ecr.png` | 依恋类型 |
| `humanity.png` | 人类浓度检测 |
| `attribute.png` | 属性测试 |

（`admin` 是管理入口，不需要图标）
