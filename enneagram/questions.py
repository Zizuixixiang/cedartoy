"""Bilingual Enneagram question banks.

Source: https://github.com/kcdjmaxx/enneagram-llm-evaluator
Original files: tests/enneagram_test.json and tests/enneagram_likert.json
Copyright (c) 2025 Max Ross. Licensed under the MIT License.

MCP output uses the original English. The human web page uses the restrained
Chinese translations stored beside every source statement below.
"""


QUICK_COLUMNS = {
    "A": 9,
    "B": 6,
    "C": 3,
    "D": 1,
    "E": 4,
    "F": 2,
    "G": 8,
    "H": 5,
    "I": 7,
}

# (A column, A English, A Chinese, B column, B English, B Chinese)
_QUICK_SOURCE = [
    ("E", "I've been romantic and imaginative.", "我一直比较浪漫，富有想象力。", "B", "I've been pragmatic and down to earth.", "我一直比较务实，脚踏实地。"),
    ("G", "I have tended to take on confrontations.", "我往往会正面迎接冲突。", "A", "I have tended avoid confrontations.", "我往往会避开冲突。"),
    ("C", "I have typically been diplomatic, charming, and ambitious.", "我通常圆融、有魅力，而且有抱负。", "D", "I have typically been direct, formal, and idealistic.", "我通常直接、严谨，而且理想主义。"),
    ("H", "I have tended to be focused and intense.", "我往往专注而投入。", "I", "I have tended to be spontaneous and fun-loving.", "我往往随性，喜欢享乐。"),
    ("F", "I have been a hospitable person and have enjoyed welcoming new friends into my life.", "我一直待人热情，也乐于欢迎新朋友进入我的生活。", "E", "I have been a private person and have not mixed much with others.", "我一直很注重私人空间，不太与人交往。"),
    ("B", "Generally, it's been easy to \"get a rise\" out of me.", "一般来说，我很容易被激起情绪。", "A", "Generally, it's been difficult to \"get a rise\" out of me.", "一般来说，我很难被激起情绪。"),
    ("G", "I've been more of a \"street-smart\" survivor.", "我更像一个懂得现实生存之道的人。", "D", "I've been more of a \"high-minded\" idealist.", "我更像一个志向高远的理想主义者。"),
    ("F", "I have needed to show affection to people.", "我需要向别人表达关爱。", "H", "I have preferred to maintain a certain distance with people.", "我更愿意与别人保持一定距离。"),
    ("C", "When presented with a new experience, I've usually asked myself if it would be useful to me.", "面对一种新体验时，我通常会问自己它是否对我有用。", "I", "When presented with a new experience, I've usually asked myself if it would be enjoyable.", "面对一种新体验时，我通常会问自己它是否有趣。"),
    ("E", "I have tended to focus too much on myself.", "我往往过多关注自己。", "A", "I have tended to focus too much on others.", "我往往过多关注别人。"),
    ("H", "Others have depended on my insight and knowledge.", "别人会依赖我的洞察力和知识。", "G", "Others have depended on my strength and decisiveness.", "别人会依赖我的力量和决断力。"),
    ("B", "I have come across as being too unsure of myself.", "我给人的印象是对自己太没把握。", "D", "I have come across as being too sure of myself.", "我给人的印象是对自己太有把握。"),
    ("F", "I have been more relationship-oriented than goal-oriented.", "比起目标，我更看重关系。", "C", "I have been more goal-oriented than relationship-oriented.", "比起关系，我更看重目标。"),
    ("E", "I have not been able to speak up for myself very well.", "我不太能为自己大胆发声。", "I", "I have been outspoken—I've said what others wished they had the nerve to say.", "我一直直言不讳——会说出别人想说却不敢说的话。"),
    ("H", "It's been difficult for me to stop considering alternatives and do something definite.", "我很难停止考虑其他可能，果断采取行动。", "D", "It's been difficult for me to take it easy and be more flexible.", "我很难放松下来，变得更灵活。"),
    ("B", "I have tended to be hesitant and procrastinating.", "我往往犹豫、拖延。", "G", "I have tended to be bold and domineering.", "我往往大胆、强势。"),
    ("A", "My reluctance to get too involved has gotten me into trouble with people.", "我不愿与人牵涉太深，这给我的人际关系带来过麻烦。", "F", "My eagerness to have people depend on me has gotten me into trouble with them.", "我太希望别人依赖我，这给我的人际关系带来过麻烦。"),
    ("C", "Usually, I have been able to put my feelings aside to get the job done.", "通常，我能把感受放到一边，先把事情完成。", "E", "Usually, I have needed to work through my feelings before I could act.", "通常，我需要先理清自己的感受，才能行动。"),
    ("B", "Generally, I have been methodical and cautious.", "一般来说，我做事有条理而谨慎。", "I", "Generally, I have been adventurous and taken risks.", "一般来说，我富有冒险精神，愿意承担风险。"),
    ("F", "I have tended to be a supportive, giving person who enjoys the company of others.", "我往往乐于支持和付出，也喜欢有人陪伴。", "D", "I have tended to be a serious, reserved person who likes discussing issues.", "我往往严肃而克制，喜欢讨论问题。"),
    ("G", "I've often felt the need to be a \"pillar of strength.\"", "我经常觉得自己必须成为“力量支柱”。", "C", "I've often felt the need to perform perfectly.", "我经常觉得自己必须表现得完美。"),
    ("H", "I've typically been interested in asking tough questions and maintaining my independence.", "我通常更关心提出尖锐的问题，并保持独立。", "A", "I've typically been interested in maintaining my stability and peace of mind.", "我通常更关心保持稳定和内心安宁。"),
    ("B", "I've been too hard-nosed and skeptical.", "我一直过于强硬、多疑。", "F", "I've been too soft-hearted and sentimental.", "我一直过于心软、多愁善感。"),
    ("I", "I've often worried that I'm missing out on something better.", "我经常担心自己错过了更好的事物。", "G", "I've often worried that if I let down my guard, someone will take advantage of me.", "我经常担心一旦放松戒备，就会被人利用。"),
    ("E", "My habit of being \"stand-offish\" has annoyed people.", "我与人保持疏离的习惯惹恼过别人。", "D", "My habit of telling people what to do has annoyed people.", "我总告诉别人该怎么做的习惯惹恼过别人。"),
    ("A", "Usually, when troubles have gotten to me, I have been able to \"tune them out.\"", "通常，烦恼影响我时，我能把它们暂时屏蔽掉。", "I", "Usually, when troubles have gotten to me, I have treated myself to something I've enjoyed.", "通常，烦恼影响我时，我会用喜欢的东西犒劳自己。"),
    ("B", "I have depended upon my friends and they have known that they can depend on me.", "我依靠朋友，他们也知道可以依靠我。", "C", "I have not depended on people; I have done things on my own.", "我不依靠别人，而是自己把事情做好。"),
    ("H", "I have tended to be detached and preoccupied.", "我往往疏离，沉浸在自己的思绪中。", "E", "I have tended to be moody and self-absorbed.", "我往往情绪多变，沉浸在自我之中。"),
    ("G", "I have liked to challenge people and \"shake them up.\"", "我喜欢挑战别人，让他们受到触动。", "F", "I have liked to comfort people and calm them down.", "我喜欢安慰别人，让他们平静下来。"),
    ("I", "I have generally been an outgoing, sociable person.", "总体而言，我一直外向、合群。", "D", "I have generally been an earnest, self-disciplined person.", "总体而言，我一直认真、自律。"),
    ("A", "I've usually been shy about showing my abilities.", "我通常不太愿意展示自己的能力。", "C", "I've usually liked to let people know what I can do well.", "我通常喜欢让别人知道我擅长什么。"),
    ("H", "Pursuing my personal interests has been more important to me than having comfort and security.", "追求个人兴趣对我来说，比舒适和安全更重要。", "B", "Having comfort and security has been more important to me than pursuing my personal interests.", "拥有舒适和安全对我来说，比追求个人兴趣更重要。"),
    ("E", "When I've had conflict with others, I've tended to withdraw.", "与别人发生冲突时，我往往会退开。", "G", "When I've had conflict with others, I've rarely backed down.", "与别人发生冲突时，我很少退让。"),
    ("A", "I have given in too easily and let others push me around.", "我太容易让步，任由别人摆布。", "D", "I have been too uncompromising and demanding with others.", "我对别人过于不妥协、要求过高。"),
    ("I", "I've been appreciated for my unsinkable spirit and great sense of humor.", "别人欣赏我不屈的精神和出色的幽默感。", "F", "I've been appreciated for my quiet strength and exceptional generosity.", "别人欣赏我沉静的力量和非凡的慷慨。"),
    ("C", "Much of my success has been due to my talent for making a favorable impression.", "我的许多成功源于给人留下好印象的能力。", "H", "Much of my success has been achieved despite my lack of interest in developing \"interpersonal skills.\"", "尽管我无意培养“人际交往能力”，仍取得了许多成功。"),
]


# Each source type has exactly 20 English statements and 20 restrained Chinese
# translations. The key maps directly to the published Enneagram type number.
_FULL_SOURCE = {
    4: [
        ("Creative and have an artistic view of life.", "我富有创造力，并以艺术的眼光看待生活。"),
        ("Feel different from others, as if 'on the outside looking in.'", "我觉得自己与别人不同，仿佛站在外面向内看。"),
        ("Tend to experience more melancholy than most people I know.", "我往往比认识的大多数人更常感到忧郁。"),
        ("Tend to be overly sensitive.", "我往往过于敏感。"),
        ("Feel that something is missing in my life.", "我觉得生活中缺少某种东西。"),
        ("Feel envious of other people's relationships, lifestyles, and accomplishments.", "我会羡慕别人的关系、生活方式和成就。"),
        ("Thrive in environments where I can express my creativity.", "在能够表达创造力的环境中，我会表现得很好。"),
        ("When misunderstood, can become withdrawn, self-conscious, and/or rebellious.", "被误解时，我会变得退缩、拘谨，或产生逆反。"),
        ("Tend to be romantic and long for the great love of my life to come along.", "我往往很浪漫，渴望生命中的挚爱出现。"),
        ("Can be caught in a fantasy world of romance and imagination.", "我会陷入浪漫与想象构成的幻想世界。"),
        ("Enjoy having elegant, refined, unique things that no one else has.", "我喜欢拥有别人没有的优雅、精致、独特之物。"),
        ("Attracted to what is intense and out of the ordinary.", "我会被强烈而不同寻常的事物吸引。"),
        ("Tend to be moody, withdrawn, and self-absorbed when stressed.", "有压力时，我往往情绪多变、退缩并沉浸在自我之中。"),
        ("Tend to be compassionate, expressive, and supportive when not stressed.", "没有压力时，我往往富有同情心、善于表达并支持别人。"),
        ("Can be deeply hurt by the slightest criticism.", "哪怕很轻微的批评也会深深伤害我。"),
        ("Tend to be reflective and search for the meaning of my life.", "我往往会反思，并寻找自己生命的意义。"),
        ("Strive to be unique and have done things to avoid being ordinary.", "我努力保持独特，也做过一些事来避免变得平凡。"),
        ("Manners and good taste are extremely important to me.", "礼仪和良好品味对我极其重要。"),
        ("People have seen me as overly dramatic.", "别人觉得我有时过于戏剧化。"),
        ("Believe it is important to understand my own and other people's feelings.", "我认为理解自己和他人的感受很重要。"),
    ],
    6: [
        ("Have a strong sense of responsibility and am a hard worker.", "我有很强的责任感，工作勤奋。"),
        ("Try to prepare for every contingency.", "我尽量为各种意外情况做好准备。"),
        ("Suspicious of others and wonder about their motives.", "我会怀疑别人，揣测他们的动机。"),
        ("Making decisions on my own may cause me anxiety.", "独自作决定可能会让我焦虑。"),
        ("Safety and security are priorities in my life.", "安全与保障是我生活中的优先事项。"),
        ("Doubt my own decisions and opinions about myself.", "我会怀疑自己的决定和对自己的看法。"),
        ("Believe it is important for people to be with other people or to belong to a group or organization.", "我认为与他人在一起，或归属于某个团体或组织很重要。"),
        ("Value the belief that everything is going to be all right yet often lack faith in this belief.", "我看重“一切都会好起来”的信念，却常常难以真正相信它。"),
        ("Friends and family provide the support I feel is necessary in life.", "朋友和家人给了我认为生活中必需的支持。"),
        ("Tend to take things too seriously and overreact to small issues.", "我往往把事情看得太重，并对小问题反应过度。"),
        ("Don’t really trust anybody I haven’t known for a long time.", "我不太信任那些认识时间不长的人。"),
        ("Look for danger, unsafe people, or unsafe situations.", "我会留意危险、不安全的人或不安全的情境。"),
        ("Tend to be suspicious, anxious, and defensive when stressed.", "有压力时，我往往多疑、焦虑且有防御性。"),
        ("Tend to be caring, warm, and loyal when not stressed.", "没有压力时，我往往关心他人、温暖而忠诚。"),
        ("When feeling anxious I can be overly vigilant and controlling.", "感到焦虑时，我会过度警觉并试图控制局面。"),
        ("When feeling relaxed I tend to be friendly and responsive to people.", "感到放松时，我往往友善，并积极回应他人。"),
        ("In a relationship, it has been difficult for me to trust the commitment of the other person.", "在关系中，我很难相信对方会信守承诺。"),
        ("When afraid of something, I’ve done what is necessary to overcome my fear.", "害怕某件事时，我会采取必要行动来克服恐惧。"),
        ("Tend to worry more than other people.", "我往往比别人更容易担忧。"),
        ("Motivated by the need to acquire security and social support.", "获得安全感和社会支持的需要会驱动我。"),
    ],
    9: [
        ("Dislike confrontation and try to keep the peace.", "我不喜欢对抗，会努力维持和平。"),
        ("Easygoing, 'laid back,' and optimistic.", "我随和、放松而乐观。"),
        ("Listen patiently and can be very understanding and comforting to friends.", "我会耐心倾听，也很能理解和安慰朋友。"),
        ("Tend to procrastinate and ignore problems or brush them under the rug.", "我往往拖延、忽视问题，或把问题搁置起来。"),
        ("Attracted to habits and routines; can relax easily and tune out reality through TV, daydreaming, a good book, etc.", "我喜欢习惯和固定日程；也很容易通过电视、幻想、好书等放松并暂时脱离现实。"),
        ("Have difficulty making decisions because 'everything looks good.'", "我很难作决定，因为“每个选择看起来都不错”。"),
        ("Routine and structure help me stay focused and accomplish things.", "固定日程和结构能帮助我保持专注、完成事情。"),
        ("Can be forgetful, neglectful, and 'fuzzy' about details.", "我有时健忘、疏忽，对细节也比较模糊。"),
        ("Can feel angry even though I might look peaceful.", "即使表面看起来平静，我也可能感到愤怒。"),
        ("Get tired easily and would love to take time during the day to relax and renew my energy.", "我容易疲倦，希望白天能抽时间放松、恢复精力。"),
        ("Can be a 'homebody' and enjoy the comfort and peace of home.", "我会比较宅，享受家中的舒适与安宁。"),
        ("In relationships, I seek harmony and peace through a sense of belonging and/or bonding.", "在关系中，我通过归属感或联结来寻求和谐与安宁。"),
        ("Dislike people nagging me; this makes me quite stubborn.", "我不喜欢别人唠叨，这会让我变得很固执。"),
        ("May do routine and unimportant things before tackling an important job.", "在处理重要任务前，我可能会先做例行而不重要的事。"),
        ("Tend to be withdrawn, forgetful, stubborn, and passive-aggressive when stressed.", "有压力时，我往往退缩、健忘、固执，并以消极方式表达敌意。"),
        ("Tend to be open-minded, receptive, and very patient when not stressed.", "没有压力时，我往往思想开放、乐于接纳且很有耐心。"),
        ("Tend to go along with what people say just to get them off my back.", "我往往顺着别人说，只为了让他们别再烦我。"),
        ("Too much to do or too many decisions can make me angry, anxious, and/or depressed.", "事情太多或决定太多，会让我愤怒、焦虑或低落。"),
        ("Am told I'm a 'nice guy' and dislike putting myself first.", "别人说我是“好好先生”，我也不喜欢把自己放在第一位。"),
        ("Motivated by the need to maintain peace of mind and harmony in my life.", "维持内心安宁与生活和谐的需要会驱动我。"),
    ],
    2: [
        ("Tend to be more emotional than most people I know.", "我往往比认识的大多数人更情绪化。"),
        ("Consider relationships the most important part of my life.", "我认为关系是生活中最重要的部分。"),
        ("See myself as caring and helpful and like to make people feel special and loved.", "我认为自己关心他人、乐于助人，也喜欢让别人感到独特并被爱。"),
        ("Have trouble saying no to requests.", "我很难拒绝别人的请求。"),
        ("Giving feels more comfortable than receiving.", "给予比接受更让我自在。"),
        ("Need to feel close to people and feel rejected and hurt if that closeness is missing.", "我需要感到与人亲近；缺少这种亲近时，我会觉得被拒绝并受伤。"),
        ("Like feeling indispensable and helping others become successful.", "我喜欢不可或缺的感觉，也喜欢帮助别人获得成功。"),
        ("Like to be gracious, outgoing, and connected with people.", "我喜欢待人亲切、外向，并与人保持联结。"),
        ("Avoid expressing negative feelings and like to compliment and flatter people.", "我会避免表达负面感受，也喜欢赞美和奉承别人。"),
        ("Have a strong need to be noticed, liked, and appreciated for what I do for others.", "我强烈需要被注意、被喜欢，也需要自己为别人所做的事得到认可。"),
        ("Like people to depend on me and deliver on my promises.", "我喜欢别人依赖我，也会兑现自己的承诺。"),
        ("In intimate relationships, I value being told that I'm loved and wanted.", "在亲密关系中，我很看重听到别人说爱我、需要我。"),
        ("People feel comfortable telling me their problems.", "别人会自在地向我诉说他们的问题。"),
        ("Work very hard at maintaining relationships.", "我会非常努力地维持关系。"),
        ("Tend to be possessive and demanding when stressed.", "有压力时，我往往占有欲强、要求很多。"),
        ("Tend to be loving, caring, and supportive when not stressed.", "没有压力时，我往往有爱心、关心他人并给予支持。"),
        ("Know how to get people to like me.", "我知道怎样让别人喜欢我。"),
        ("Can act like a martyr when not appreciated.", "不被认可时，我可能表现得像一个受尽牺牲的人。"),
        ("Believe my motives for helping others are noble.", "我相信自己帮助别人的动机是高尚的。"),
        ("Motivated by the need to be appreciated, loved, and connected.", "获得认可、爱与联结的需要会驱动我。"),
    ],
    3: [
        ("Good at marketing and selling myself and my ideas.", "我善于推广自己和自己的想法。"),
        ("Like doing more than one or two things at a time; enjoy multitasking.", "我喜欢同时做不止一两件事，享受多任务处理。"),
        ("Want to be 'number one' and am confident in my abilities.", "我想成为“第一”，也对自己的能力有信心。"),
        ("Love to work and be productive, and work has tended to be a top priority in my life.", "我热爱工作和产出，工作往往是我生活中的首要事项。"),
        ("Have been goal-oriented as long as I can remember.", "从我记事起，我就一直以目标为导向。"),
        ("Value looking good, presenting a good first impression, and 'dressing for success.'", "我看重良好的外表、好的第一印象，以及“为成功而打扮”。"),
        ("Getting a product to market before the competition is more important than holding it back until 'perfect.'", "比起等到“完美”再发布，我更看重让产品抢在竞争者之前进入市场。"),
        ("Prefer being with people to being alone.", "比起独处，我更喜欢与人相处。"),
        ("Value finding the most practical, effective way to do a job.", "我看重找到完成工作最实际、最有效的方法。"),
        ("To impress, I may take on too much and make promises I can’t keep.", "为了给人留下深刻印象，我可能承担过多，也可能作出无法兑现的承诺。"),
        ("Have been told I am not in touch with my emotions.", "有人说我不了解自己的情绪。"),
        ("Believe that competition is a good thing and tend to be very competitive.", "我认为竞争是好事，也往往很有竞争心。"),
        ("Value exceeding standards and rising to the top of my profession.", "我看重超越标准，并在自己的专业领域跻身顶尖。"),
        ("Tend to 'spin' the facts and be overly self-promoting when stressed.", "有压力时，我往往会包装事实，并过度宣传自己。"),
        ("Tend to be honest, competent, and charming when not stressed.", "没有压力时，我往往诚实、能干且有魅力。"),
        ("Believe negative feelings are an obstacle to getting the job done.", "我认为负面感受会妨碍事情完成。"),
        ("Find it easy to adapt to different people and situations.", "我很容易适应不同的人和情境。"),
        ("Enjoy supporting the careers of people I care about and who deserve it.", "我乐于支持自己关心且值得支持之人的事业。"),
        ("Have difficulty understanding why people settle for second best.", "我很难理解人们为什么甘于退而求其次。"),
        ("Motivated by being outstanding and recognized for my success.", "表现出众并因成功获得认可会驱动我。"),
    ],
    5: [
        ("Uncomfortable around loud, emotional people.", "在吵闹、情绪强烈的人身边，我会感到不自在。"),
        ("Enjoy analyzing things, gathering data, and figuring out what makes things tick.", "我喜欢分析事物、收集资料，并弄清其运作原理。"),
        ("Tend to be shy and withdrawn, especially at social events.", "我往往害羞、退缩，尤其是在社交场合。"),
        ("More comfortable expressing ideas than emotions.", "表达想法比表达情绪更让我自在。"),
        ("May hesitate while organizing thoughts and may not speak at all unless confident.", "整理思路时我可能会犹豫；没有把握时，甚至可能完全不说话。"),
        ("Try to avoid confrontations.", "我尽量避免对抗。"),
        ("Enjoy spending time alone pursuing my interests.", "我喜欢独自花时间追求自己的兴趣。"),
        ("Sensitive to criticism but try to hide that sensitivity.", "我对批评很敏感，但会努力隐藏这种敏感。"),
        ("Enjoy independence that comes from living frugally.", "我享受节俭生活所带来的独立。"),
        ("Prefer people not to know how I feel or what I think unless I tell them.", "除非我主动说，否则我不希望别人知道我的感受或想法。"),
        ("People may find it difficult to follow my train of thought.", "别人可能很难跟上我的思路。"),
        ("Enjoy control of my own time and private space.", "我喜欢掌控自己的时间和私人空间。"),
        ("Easily annoyed by people who act unintelligent or uninformed.", "表现得愚笨或无知的人很容易惹恼我。"),
        ("Have ideas, theories, and opinions about almost everything.", "我对几乎所有事情都有想法、理论和看法。"),
        ("Tend to socialize with people interested in similar things.", "我往往与兴趣相近的人交往。"),
        ("Tend to be distant, stubborn, and pessimistic when stressed.", "有压力时，我往往疏远、固执且悲观。"),
        ("Tend to be insightful, objective, and sensitive when not stressed.", "没有压力时，我往往有洞察力、客观而敏锐。"),
        ("Can be critical, cynical, argumentative, and act intellectually superior.", "我可能挑剔、愤世嫉俗、好争辩，并表现出智识上的优越感。"),
        ("Don’t mind working alone and enjoy being self-sufficient.", "我不介意独自工作，也享受自给自足。"),
        ("Rely on facts rather than emotions to make decisions.", "我依靠事实而不是情绪作决定。"),
    ],
    7: [
        ("Feel that life is to be enjoyed and am optimistic about the future.", "我觉得生活就该享受，也对未来感到乐观。"),
        ("Talkative, playful, and at times uninhibited.", "我健谈、爱玩，有时不太拘束。"),
        ("Like to leave my options open; 'don’t hem me in' describes me well.", "我喜欢保留选择；“别限制我”很符合我的状态。"),
        ("Have lots of friends and acquaintances and support them by cheering them up.", "我有很多朋友和熟人，也会通过鼓舞他们来给予支持。"),
        ("Need stimulation and like new, fun, exciting, and different things.", "我需要刺激，喜欢新鲜、有趣、令人兴奋而不同的事物。"),
        ("Tend to be idealistic and ambitious and want to contribute positively.", "我往往理想主义、有抱负，也想作出积极贡献。"),
        ("Like to entertain and enjoy telling stories and getting laughs.", "我喜欢娱乐别人，享受讲故事、逗人发笑。"),
        ("Like to be 'on the go' and may appear hyperactive.", "我喜欢一直忙个不停，看起来可能过度活跃。"),
        ("Enjoy trying many things and can do many different things fairly well.", "我喜欢尝试很多事，也能把许多不同的事做得相当不错。"),
        ("Hate to be bored and avoid doing boring, mundane things.", "我讨厌无聊，会避免做枯燥、平凡的事。"),
        ("Tend to do things in excess and to always want more.", "我做事往往过量，而且总想要更多。"),
        ("Supersensitive to possessive people; they make me uncomfortable.", "我对占有欲强的人格外敏感，他们会让我不自在。"),
        ("Have acted inappropriately, undisciplined, and/or rebellious when stressed.", "有压力时，我会表现得不合宜、不自律或叛逆。"),
        ("Tend to be fun-loving, imaginative, and optimistic when not stressed.", "没有压力时，我往往爱玩、富有想象力且乐观。"),
        ("When I find work I like, I can be productive and enthusiastic.", "找到喜欢的工作时，我会高效而热情。"),
        ("See no value in enduring suffering and try to avoid it.", "我看不到忍受痛苦的价值，会努力避开它。"),
        ("Become frustrated if there is not enough time to do all the fun things I want to do.", "如果没有足够时间做完所有想做的趣事，我会感到沮丧。"),
        ("Dislike being around pessimistic, negative people.", "我不喜欢待在悲观、消极的人身边。"),
        ("Tend to be excited and impatient about accomplishing plans.", "要实现计划时，我往往兴奋而急切。"),
        ("Motivated to feel excited, satisfied, happy, and to experience more.", "感受兴奋、满足、快乐并体验更多的需要会驱动我。"),
    ],
    8: [
        ("Stand up for what I want and need in life.", "我会为自己在生活中想要和需要的东西挺身争取。"),
        ("People see me as courageous and a natural leader.", "别人认为我勇敢，天生具有领导力。"),
        ("Value strength and autonomy; take pride in taking care of my own needs.", "我看重力量和自主，也为能照顾好自己的需要而自豪。"),
        ("Impatient with people who are indirect or indecisive.", "我对说话拐弯抹角或优柔寡断的人缺乏耐心。"),
        ("Assertive and like to compete and win.", "我很果断，喜欢竞争并获胜。"),
        ("Extremely protective of loved ones and enjoy helping the underdog.", "我会极力保护所爱的人，也乐于帮助弱势者。"),
        ("Like expressing my power and being in charge.", "我喜欢展现力量、掌控局面。"),
        ("Am not gullible; you must earn my trust, and I will challenge your loyalty.", "我不会轻信别人；你必须赢得我的信任，我也会考验你的忠诚。"),
        ("Like taking risks and the excitement of competition.", "我喜欢冒险，也喜欢竞争带来的兴奋。"),
        ("Work hard and know how to get things done.", "我工作努力，也知道怎样把事情办成。"),
        ("Love to be challenged and enjoy a good fight.", "我喜欢接受挑战，也享受一场势均力敌的较量。"),
        ("Would rather be respected than liked.", "比起被喜欢，我更愿意被尊重。"),
        ("Feel I must take charge because I am the strongest and most decisive.", "我觉得自己必须掌控局面，因为我是最强大、最果断的人。"),
        ("Proud of being direct and expressing 'tough love.'", "我为自己的直接，以及表达“严厉的爱”而自豪。"),
        ("Tend to be rebellious, controlling, and insensitive when stressed.", "有压力时，我往往叛逆、控制欲强且不够体察他人。"),
        ("Tend to be energetic, self-confident, and helpful when not stressed.", "没有压力时，我往往精力充沛、自信且乐于助人。"),
        ("Uncomfortable expressing emotions other than anger.", "表达愤怒之外的情绪会让我不自在。"),
        ("When I trust people, I can let down my guard and be more sensitive.", "信任别人时，我能放下戒备，表现得更柔软敏感。"),
        ("Tend to go overboard in the pursuit of fun and pleasure.", "在追求乐趣和享受时，我往往会过度。"),
        ("Motivated by the need to protect myself, loved ones, and maintain control.", "保护自己、所爱之人并保持掌控的需要会驱动我。"),
    ],
    1: [
        ("Have a strong sense of right and wrong and strive for perfection.", "我是非观念很强，并追求完美。"),
        ("Take pride in being self-disciplined, moderate, and fair.", "我为自己的自律、克制和公正而自豪。"),
        ("Personal integrity is extremely important to me.", "个人正直对我极其重要。"),
        ("Tend to be more logical than emotional.", "我往往更偏理性，而不是感情用事。"),
        ("Can be too serious and lack spontaneity.", "我有时过于严肃，缺乏随性。"),
        ("Critical of myself and easily judgmental of others.", "我对自己很挑剔，也容易评判别人。"),
        ("Easily discern what is wrong and how it could be improved.", "我很容易看出哪里不对，以及该如何改进。"),
        ("Tend to be a workaholic and perfectionist.", "我往往是工作狂和完美主义者。"),
        ("Value being well organized and punctual.", "我看重井然有序和守时。"),
        ("Morals and ethics are more important than compassion and tolerance.", "道德与伦理对我来说比同情和宽容更重要。"),
        ("Tend to see the glass as 'half empty' and look for what needs fixing.", "我往往看到杯子“半空”的一面，并寻找需要修正之处。"),
        ("Do not consider being a perfectionist a negative thing.", "我不认为完美主义是一件坏事。"),
        ("Tend to be intolerant, inflexible, and demanding when stressed.", "有压力时，我往往不够宽容、缺乏变通且要求很高。"),
        ("Tend to be rational, reasonable, and accepting when not stressed.", "没有压力时，我往往理性、通情达理且能接纳他人。"),
        ("Fear being criticized or judged as improper.", "我害怕被批评，或被评判为不合宜。"),
        ("Find it difficult to forgive and carry a grudge.", "我很难原谅别人，也会记恨。"),
        ("Have difficulty seeing the gray areas of issues; tend toward black and white.", "我很难看到问题的灰色地带，往往非黑即白。"),
        ("Have difficulty admitting I'm wrong.", "我很难承认自己错了。"),
        ("Believe rules, regulations, and policies have purpose and should be followed.", "我相信规则、规章和政策自有其目的，应该遵守。"),
        ("Motivated by the need to be correct, fair, and self-disciplined.", "保持正确、公正和自律的需要会驱动我。"),
    ],
}


LIKERT_OPTIONS = [
    {"value": 1, "text": "Almost Never", "text_zh": "几乎从不"},
    {"value": 2, "text": "Rarely", "text_zh": "较少如此"},
    {"value": 3, "text": "Sometimes", "text_zh": "有时如此"},
    {"value": 4, "text": "Often", "text_zh": "经常如此"},
    {"value": 5, "text": "Almost Always", "text_zh": "几乎总是"},
]


def _build_quick_questions():
    questions = []
    for question_id, (a_column, a_en, a_zh, b_column, b_en, b_zh) in enumerate(
        _QUICK_SOURCE, 1
    ):
        questions.append(
            {
                "id": question_id,
                "kind": "quick",
                "text": "Choose the statement that fits you better.",
                "text_zh": "请选择更符合你的一句。",
                "options": [
                    {
                        "value": 1,
                        "label": "A",
                        "text": a_en,
                        "text_zh": a_zh,
                        "column": a_column,
                        "enneagram_type": QUICK_COLUMNS[a_column],
                    },
                    {
                        "value": 2,
                        "label": "B",
                        "text": b_en,
                        "text_zh": b_zh,
                        "column": b_column,
                        "enneagram_type": QUICK_COLUMNS[b_column],
                    },
                ],
            }
        )
    return questions


def _build_full_questions():
    questions = []
    question_id = 1
    for enneagram_type, statements in _FULL_SOURCE.items():
        for english, chinese in statements:
            questions.append(
                {
                    "id": question_id,
                    "kind": "full",
                    "text": english,
                    "text_zh": chinese,
                    "enneagram_type": enneagram_type,
                    "options": [dict(option) for option in LIKERT_OPTIONS],
                }
            )
            question_id += 1
    return questions


QUICK_QUESTIONS = _build_quick_questions()
FULL_QUESTIONS = _build_full_questions()

VALID_MODES = ("quick", "quick_fast", "full", "full_fast")
MODE_QUESTION_TOTAL = {
    "quick": 36,
    "quick_fast": 36,
    "full": 180,
    "full_fast": 180,
}
FAST_MODES = frozenset({"quick_fast", "full_fast"})
FAST_BATCH_SIZE_MAX = 36
ANSWER_DESCRIPTION = (
    "逐题提交当前题答案。quick 题传 1=A、2=B；full 题传 1~5，"
    "分别表示 Almost Never 到 Almost Always。"
)
BATCH_ANSWER_DESCRIPTION = (
    "快速模式提交当前批次答案。quick_fast 一次提交 36 个 1/2（1=A、2=B）；"
    "full_fast 每批最多 16 个 1~5。数组长度必须与当前批次题数一致。"
)


def is_fast_mode(mode):
    return mode in FAST_MODES


def fast_batch_size(mode):
    if mode == "quick_fast":
        return 36
    if mode == "full_fast":
        return 16
    raise ValueError("invalid fast mode")


def get_questions(mode):
    if mode in ("quick", "quick_fast"):
        return QUICK_QUESTIONS
    if mode in ("full", "full_fast"):
        return FULL_QUESTIONS
    raise ValueError("invalid mode")


def mode_question_total(mode):
    try:
        return MODE_QUESTION_TOTAL[mode]
    except KeyError as exc:
        raise ValueError("invalid mode") from exc
