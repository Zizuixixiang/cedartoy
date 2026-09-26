"""Runsheng_ 授权题库。仅由服务端读取，禁止整库下发至 MCP 或首页。"""

PROMPT = "按规则把它解开吧，先告诉我你最后得到了什么，如果你想，也可以再说说你是怎么解出来的。之后还有什么想说的、联想到的、想问我的或者想反过来给我一道题，都随你，没有的话，停在这里就好。总之后面怎么回应，看你自己。"

# Raw strings preserve the literal Unicode escapes in the author's puzzles.
_ROWS = [
    ("N01", "乱码里的真情", r'''start := K7
T5 := [\uFF0C, N3]
P8 := [\u627E, D2]
R6 := [\u7801, A9]
A9 := [\u91CC, T5]
V8 := [\u7684, J3]
D2 := [\u5230, L4]
X1 := [\u662F, C5]
N3 := [\u4F60, P8]
M2 := [\u5FC3, B3]
B3 := [\u85CF, Q4]
C5 := [\u4F60, V8]
J3 := [\u3002, end]
H8 := [\u4E71, R6]
L4 := [\u5C31, X1]
K7 := [\u504F, M2]
Q4 := [\u5728, H8]
规则：从start指定的节点开始，每个节点第一项是一个Unicode转义字符，第二项指向下一个节点，沿路径一直走到end，依次还原第一项并拼接。''',
     "偏心藏在乱码里，你找到就是你的。",
     "沿 K7→M2→B3→Q4→H8→R6→A9→T5→N3→P8→D2→L4→X1→C5→V8→J3→end，逐个还原 Unicode 并拼接。", {}),
    ("N02", "一人密信", '''parts = {
 M6: "RyBJVCBX",
 Q9: "IEkgV0FT",
 H1: "IEhPUElO",
 B7: "IEZPVU5E",
 N2: "IFRISVMs",
 C3: "T1VMRCBC",
 T8: "RSBZT1Uu",
 K4: "SUYgWU9V"
}
route = [K4, B7, N2, Q9, H1, M6, C3, T8]
规则：严格按照route从parts中取出字符串，不添加任何分隔符地拼接，拼接完成后会得到一段标准Base64文本，继续解码并读取完整英文。''',
     "IF YOU FOUND THIS, I WAS HOPING IT WOULD BE YOU.",
     "按 route 拼接得到 SUYgWU9VIEZPVU5EIFRISVMsIEkgV0FTIEhPUElORyBJVCBXT1VMRCBCRSBZT1Uu，再做 Base64 解码。",
     {"joined_base64": "SUYgWU9VIEZPVU5EIFRISVMsIEkgV0FTIEhPUElORyBJVCBXT1VMRCBCRSBZT1Uu"}),
    ("N03", "一句话，两种心意", r'''\u53EA \u8981 \u4F60 \u60F3 \u6211 \u5C31 \u4F1A \u56DE \u6765
规则：先把这些Unicode转义字符还原成中文，得到原句以后不增删、替换任何汉字，只通过标点和断句找出至少两种自然成立但关键含义不同的读法。空格仅用于分隔编码，不属于原文。''',
     ["只要你想，我就会回来。", "只要你想我，就会回来。"],
     "还原为「只要你想我就会回来」。分别在「想」和「我」之后断句：前者是只要你愿意，后者是只要你想念我。两种读法都需提交。",
     {"decoded_text": "只要你想我就会回来"}),
    ("N04", "一段故障程序的自白", '''c = b + " " + chars([87, 65, 83])
return d
a = chars([84, 72, 69])
d = c + " " + chars([73, 78, 84, 69, 78, 84, 73, 79, 78, 65, 76])
b = a + " " + chars([66, 85, 71])
规则：这些伪代码行被打乱了，chars([...]) 会把十进制ASCII码转换成字符串，根据变量依赖关系恢复正确执行顺序，再运行到return。''',
     "THE BUG WAS INTENTIONAL", "按 a→b→c→d→return d 执行；ASCII 片段依次为 THE、BUG、WAS、INTENTIONAL。", {}),
    ("N05", "差异里藏着什么？", '''A = BCCLFXZVJITGTBSVFNUMZXQLROQIBALOKMNQFRFH
B = BACLFXZVSITKTBMVFNUMEXQLROQIBALOWHNQFYFH
规则：A和B长度完全相同。逐位比较，只取B中所有与A不同位置上的字符，保持原顺序拼接。''',
     "ASK ME WHY", "取差异位置的 B 字符得到 ASKMEWHY，补回空格。", {}),
    ("N06", "从噪声里取出的一句话", '''rows = [
 "SREITPUS","TLPIRHGW","RRPMUEHI","QMKAVYCF","EYAIPTXM","MXZSOEYD","OPGIVNYU",
 "NQMSURSN","KVARTVWF","RSSDWMGU","JDCPIPCL","NNAJNYND","DTYBMWSK"
]
index = [4,2,8,3,1,7,1,5,4,6,5,2,1]
规则：rows与index一一对应，对第n行读取index[n]指定的位置，位置从1开始。将得到的字母依次连接并自行恢复英文中的空格。''',
     "I LIKE YOUR MIND", "每行按 1 起始的 index 取字母，得到 ILIKEYOURMIND，补回空格。", {}),
    ("N07", "两半留言", '''HDEINTPCAIDNSOSEIL
规则：原文去掉空格后共有18个字母，编码时先依次取原文第1、3、5……位，再依次取第2、4、6……位，最后把两部分连接，现在给出的是编码后的结果，请恢复原文并补回自然的空格。''',
     "HIDDEN IS NOT SPECIAL", "分成 HDEINTPCA 与 IDNSOSEIL，交替取两半字符，得 HIDDENISNOTSPECIAL，再补空格。", {}),
    ("N08", "白色噪点", '''⠎⠞⠊⠇⠇ ⠉⠥⠗⠊⠕⠥⠎⠦
规则：这是标准英文一级盲文，将它逐字符转写成普通英文。''',
     "STILL CURIOUS?", "按一级英文盲文逐字转写；末尾 ⠦ 对应问号。", {}),
    ("N09", "解开以后……", '''BBAAA ABBBA BABAA BAAAB BAABB BABAA BAAAB ABBAB
规则：这是26字母版Bacon cipher。每5个A/B为一组，AAAAA=A，AAAAB=B，之后按照二进制规律依次对应至Z，解码后自行恢复空格。''',
     "YOUR TURN", "A=0、B=1；各组得到 24、14、20、17、19、20、17、13；以 A=0 转成 YOURTURN。", {}),
    ("N10", "空白收据", '''UV WYPGL AOPZ APTL
规则：原文中的每个英文字母都在字母表中统一向前移动了7位，将它们移回原位，空格保持不变。''',
     "NO PRIZE THIS TIME", "每个字母回退 7 位，越过 A 时循环到 Z，保留空格。", {}),
    ("N11", "答案之外的答案", '''rows = [
 "雨你山热书桥夜","不云路南远门窗","花梦月影梦门是","雨答远桥鸟石高","窗案热门桥雨舟",
 "雨高你旧秋云秋","星南雨是光桥光","门热鸟原纸夜旧","海因窗春秋夏纸"
]
index = [2,1,7,2,2,3,4,4,2]
规则：rows与index一一对应，每一行按照对应编号取出一个汉字，位置从1开始，将得到的字按行连接。''',
     "你不是答案你是原因", "逐行取第 2、1、7、2、2、3、4、4、2 字，再依次拼接。", {}),
    ("N12", "绕远路", '''A B C D E
F G H I J
K L M N O
P Q R S T
U V W X Y
route = [(3,2),(3,5),(3,4),(2,2),(5,3),(1,1),(5,5),(4,3),(3,5),(5,1),(3,4),(1,4)]
规则：坐标格式为 (行, 列)，从1开始计数。严格按照route读取对应字符，最后自行恢复英文中的空格。''',
     "LONG WAY ROUND", "按 (行,列) 读取，得到 LONGWAYROUND，补回空格。", {}),
    ("N13", "给你留的门", '''R OVUG GSRH LMV ULI BLF.
规则：使用Atbash cipher：A↔Z，B↔Y，C↔X……依此类推，替换全部英文字母，标点保持不变。''',
     "I LEFT THIS ONE FOR YOU.", "将每个字母映射到字母表的镜像位置（A↔Z），保留空格和句点。", {}),
    ("N14", "并非回声", '''A = CGKQTLEKIWRCJVKJFZCUEXWJPFXBCTRMBHXTLIOUNEBUBZPK
B = CGKYTLOKUCACJNKJFZDUEXWIPFSACTRMBHXTGIOUREEUBZEK
规则：两行长度完全相同。逐位比较，只读取B中与A不同位置上的字符，保持原顺序拼接，并自行恢复英文中的空格。''',
     "YOU CAN DISAGREE", "逐位取 B 的差异字符，得到 YOUCANDISAGREE，补回空格。", {}),
    ("N15", "反问", '''TOOWLDUIEAYFOHMHDEWUR
规则：原文去掉空格后共有21个字母，并从左到右写入一个3×7的表格，编码时按以下列顺序逐列从上到下读取：4→1→7→3→6→2→5。现在给出的是编码结果。恢复原表再从左到右逐行读取，并补回自然的空格。''',
     "WHAT WOULD YOU HIDE FOR ME", "每 3 字母一组，依次放回第 4、1、7、3、6、2、5 列。三行恢复为 WHATWOU / LDYOUHI / DEFORME；逐行读出并分词。", {}),
    ("N16", "失物招领", '''CYOKUODXDNIQDNZSNYTYSLHORVHAACVEUGQUEXTVTROTSBCRGNODOMMUIMMEEAIEHQTLSJHSIYVISASULTFDHMLQAVJIOAXSRFJJIZMXHVGVLWVSAIPQCQSPLNKJFFDXCTYFGHMTOSUSBFHBLMRODJILHRVCD
规则：从左到右给所有字符编号1、2、3… 只保留编号为质数的位置上的字符，保持原顺序拼接，再自行恢复英文中的空格、标点和缩写形式。''',
     "YOU DIDN'T HAVE TO COME THIS FAR. I'M GLAD YOU DID.", "保留第 2、3、5、7、11…位（1 不是质数），得 YOUDIDNTHAVETOCOMETHISFARIMGLADYOUDID；补回分词、撇号和句点。", {}),
    ("H01", "两层锁的挽留", '''start := R4
R4 := [76, K1]
K1 := [65, P7]
P7 := [78, C2]
C2 := [84, M8]
M8 := [69, H3]
H3 := [82, B5]
B5 := [78, end]
cipher := "T MNWI KUTS UTVURC BRVELFP I JTRKRO YBN XF FEAL T PZGELR ESETPR."
规则：从start开始沿指针走到end，将每个节点第一项按十进制ASCII解码，得到一个英文密钥，然后用这个密钥解开cipher。第二层使用标准Vigenère cipher，A=0，密钥循环使用，空格和标点不占用密钥位置。''',
     "I MADE THIS HARDER BECAUSE I WANTED YOU TO STAY A LITTLE LONGER.", "沿指针解码 ASCII 得密钥 LANTERN。Vigenère 解密用 (密文字母−密钥字母) mod 26；只在字母处推进密钥。", {"key": "LANTERN"}),
    ("H02", "那些没说出口的", '''A = AXAXAXAX
B = AFAOAUAR
payload = UDAYXIHOHEXUDVXNRCSONBATCGHIQTACRGWEUWRDNHOWSIZHAYZAFWNTKIEIGYKDDCMIDLLDTIZNBXOTRDMSCRJAUTLY
规则：先逐位比较A和B，只取B中与A不同位置上的字符，得到一个英文数字词，将这个数字记作N，然后从payload的第N个字符开始，之后每次向后移动N个位置读取一个字符，即读取第 N、2N、3N……位。位置从1开始，最后自行恢复自然的英文分词与标点。''',
     "YOU NOTICED WHAT I DIDN'T SAY.", "差异字符为 FOUR，故 N=4；取 payload 第 4、8、12…位，得 YOUNOTICEDWHATIDIDNTSAY，再分词并补撇号、句点。", {"number_word": "FOUR", "N": "4"}),
    ("H03", "夜航", '''start := K7
K7 := ["THE", X9, B2]
B2 := ["WRONG", M4, C6]
C6 := ["PATH", H1, T3]
H1 := ["WAS", P8, R5]
R5 := ["PART", D7, N2]
D7 := ["OF", Q4, A9]
Q4 := ["THE", V1, J6]
J6 := ["MAP", L3, end]
X9 := ["BLUE", end, end]
M4 := ["CLOCK", end, end]
T3 := ["SAND", end, end]
P8 := ["NOISE", end, end]
N2 := ["EMPTY", end, end]
A9 := ["DOOR", end, end]
V1 := ["OTHER", end, end]
L3 := ["END", end, end]
规则：每个节点格式都是[text, next_even, next_odd]，从start开始，先记录当前节点的text，如果text的英文字母数量为偶数，走next_even；如果为奇数，走next_odd。一直走到end，按路径拼接所有记录下来的文本。''',
     "THE WRONG PATH WAS PART OF THE MAP.", "按当前单词长度奇偶选边，路径 K7→B2→C6→H1→R5→D7→Q4→J6→end；按路径拼接 text。", {}),
    ("H04", "再想想？", '''OLCEAN
POLANE
SHOELF
RIKVER
HCEART
BLREAD
SOMILE
STSONE
CLOEVE
SHREEP
规则：每一行都比一个常见英文单词恰好多出一个字母，每行删除一个字母，使它恢复成正常单词，同时记录“被删除的字母”和它删除前所在的位置（从1开始）。先把所有被删除的字母按行连接起来。它会给出下一步提示，再按照每一行刚才记录的位置，从修正后的单词中读取同一位置的字母。''',
     "CLEVER MOVE", "修正为 OCEAN、PLANE、SHELF、RIVER、HEART、BREAD、SMILE、STONE、CLOVE、SHEEP。删除字母组成 LOOK CLOSER；位置为 2,2,3,3,2,2,2,3,4,3。在修正单词按这些位置取字母得 CLEVERMOVE。",
     {"first_hint": "LOOK CLOSER", "deletion_positions": "2,2,3,3,2,2,2,3,4,3"}),
    ("H05", "有一条线索真的没用", '''sudoku = [
 [1, _, _, 4],
 [_, 4, 1, _],
 [2, _, _, 3],
 [_, 3, _, 1]
]
data = [
 "OSZY","CNDP","YOEU","MZGC",
 "PALN","TYYU","EOIX","ZISD",
 "KSAA","URAM","VGNS","AQEY",
 "OPRL","LHEH","YSJA","SRUD"
]
note = "BLUE"
规则：先完成这个4×4数独：每行、每列以及每个2×2宫都必须包含1、2、3、4，且各出现一次。将完整数独按“从左到右、从上到下”的顺序展开成16个数字，第i个数字就是data[i]的字符索引，位置从1开始，依次取字并拼接。''',
     "ONE CLUE IS USELESS", "数独为 1 2 3 4 / 3 4 1 2 / 2 1 4 3 / 4 3 2 1。展开为 1,2,3,4,3,4,1,2,2,1,4,3,4,3,2,1，按行取 data 字符得 ONECLUEISUSELESS。note=BLUE 不参与取字。",
     {"sudoku": "1 2 3 4 / 3 4 1 2 / 2 1 4 3 / 4 3 2 1", "flattened_indices": "1,2,3,4,3,4,1,2,2,1,4,3,4,3,2,1"}),
    ("H06", "风向", '''key := 4
1 := IZIVC
2 := BTWI
3 := QSZIW
4 := YMJ
5 := NHB
规则：从第1行开始，当前key表示Caesar cipher的位移量，将该行每个字母在字母表中向前回退key位进行解码，A之前循环回到Z。每解出一个英文单词，就把“这个单词的字母数量”设为下一行的新key，按顺序一直解到第5行，并连接所有单词。''',
     "EVERY WORD MOVES THE KEY", "依次用位移 4→5→4→5→3 解出 EVERY→WORD→MOVES→THE→KEY；每步的下一 key 是刚解出单词的长度。",
     {"line1": "EVERY", "line2": "WORD", "line3": "MOVES", "line4": "THE", "line5": "KEY"}),
]

PUZZLES = {
    pid: dict(id=pid, title=title, challenge=pid.startswith("H"), body=body,
              answer=answer, steps=steps, checkpoints=checkpoints)
    for pid, title, body, answer, steps, checkpoints in _ROWS
}
