"""玩家指令文本归一化。

有些 AI 模型在调用 MCP 工具时会把分隔用的空格写成全角空格（U+3000）、
不间断空格（U+00A0）等 Unicode 空白，导致游戏引擎里
`startswith("买 ")` / `split(" ")` 这类按 ASCII 空格匹配的解析全部落空，
游戏只能回一句“没听懂”。

所以在指令进入各游戏引擎之前，统一把这些 Unicode 空白折叠成 ASCII 空格。

注意：
* `str.split()`（不带参数）本来就能切 U+3000/U+00A0，所以只有
  `startswith("X ")` 和 `split(" ")` 这两类写法会踩坑。
* `str.strip()` **不会**去掉 U+FEFF（它的 `isspace()` 是 False），
  所以必须“先替换、再 strip”；反过来会给开头留一个 ASCII 空格。
* 只折叠 Unicode 空白，不碰 ASCII 空格，避免改变本来就能跑通的指令
  （例如自由文本里的连续空格）。
"""

import re


# 语义上确实是“空格”的 Unicode 字符：NBSP、Ogham 空格、
# en/em 系列（含 U+2002/2003/2009/200A）、窄 NBSP、数学空格、全角空格，
# 外加常被误粘进来的 BOM / ZWNBSP。
_UNICODE_SPACES = re.compile(
    "["
    " "      # NO-BREAK SPACE
    " "      # OGHAM SPACE MARK
    " - "  # EN QUAD .. HAIR SPACE
    " "      # NARROW NO-BREAK SPACE
    " "      # MEDIUM MATHEMATICAL SPACE
    "　"      # IDEOGRAPHIC SPACE（全角空格）
    "﻿"      # ZERO WIDTH NO-BREAK SPACE / BOM
    "]+"
)


def normalize_command_spaces(command):
    """把指令里的 Unicode 空白折叠成 ASCII 空格，并去掉首尾空白。

    非字符串原样返回，交给各调用方自己的类型校验去报错。

    >>> normalize_command_spaces("买　番茄")
    '买 番茄'
    >>> normalize_command_spaces("﻿买 番茄")
    '买 番茄'
    """
    if not isinstance(command, str):
        return command
    return _UNICODE_SPACES.sub(" ", command).strip()
