"""Opt-in strict lifecycle for research scales, using the existing test tables.

Old scales keep their original engine behavior. created_at identifies a run;
completed receipts live in its private result_detail, so save/claim logic can
continue to move the existing test_sessions/test_results records together.
"""
import hashlib
import json
import secrets
import time
from types import SimpleNamespace

from scale_test_engine import ScaleTestEngine, JsonRpcError, PLAYER_ID_RE, MAX_SESSIONS
from .interpretation import build_interpretation, format_interpretation

DISCLAIMER = '仅供娱乐/自我了解，不作诊断；小机作答仅作娱乐演绎，不具有人类量表效度。'


class ResearchScaleEngine(ScaleTestEngine):
    PLAYER_ID_RE = PLAYER_ID_RE

    def __init__(self, spec, db_path):
        self.spec = spec
        self.items = spec['items']
        self.result_dimensions = {**spec['dimensions'], **({'overall': '全量表均分'} if spec.get('overall_mean') else {})}
        difference = spec.get('mean_difference')
        if difference:
            assert spec['aggregation'] == 'mean'
            assert all(difference[k] in spec['dimensions'] for k in ('minuend', 'subtrahend'))
            self.result_dimensions[difference['key']] = difference['label']
        values = [o['value'] for o in spec['options']]
        assert self.items and values == list(range(min(values), max(values) + 1))
        assert spec['aggregation'] in ('sum', 'mean')
        assert [q['id'] for q in self.items] == list(range(1, len(self.items) + 1))
        assert all(q['dimension'] in spec['dimensions'] and type(q['reverse']) is bool for q in self.items)
        assert all(q.get('original') and q.get('text') for q in self.items)
        self.notice = ('仅供娱乐/自我了解，不作诊断。' if spec.get('human_only') else DISCLAIMER) + ' ' + spec['notice']
        self.attribution = f"{spec['author']} · {spec['source']} · {spec['license']}"
        reverse_ids = [str(q['id']) for q in self.items if q['reverse']]
        self.scoring_note = spec.get('scoring_note') or (
            (f"第{', '.join(reverse_ids)}题按 {min(values) + max(values)}−原分计分；" if reverse_ids else '无反向题；') + '各维度'
            + ('求和。' if spec['aggregation'] == 'sum' else '取均值。')
            + ('另报告全题均分。' if spec.get('overall_mean') else '')
            + '仅呈现原始分数，无诊断阈值或常模百分位。'
        )
        questions = [{**q, 'options': spec['options']} for q in self.items]
        provider = SimpleNamespace(VALID_MODES=('full', 'full_fast'),
            get_questions=lambda mode: questions,
            is_fast_mode=lambda mode: mode == 'full_fast',
            fast_batch_size=lambda mode: len(questions), FAST_BATCH_SIZE_MAX=len(questions))
        super().__init__(game=spec['game'], title=spec['title'], db_path=db_path,
            questions=provider, scoring=None, answer_min=min(values), answer_max=max(values),
            prompt=spec['instruction'], supports_compare=False)

    def ordered_items(self, session_id=None):
        if not self.spec.get('randomize') or session_id is None:
            return self.items
        # Stable per saved run, including cross-process/identity-claim restores.
        # Keep this v1 ordering algorithm stable for existing sessions.
        return sorted(self.items, key=lambda q: hashlib.sha256(
            f'{self.game}:order-v1:{session_id}:{q["id"]}'.encode()).digest())

    def score(self, answers, session_id=None):
        groups = {key: [] for key in self.spec['dimensions']}
        for item, value in zip(self.ordered_items(session_id), answers):
            groups[item['dimension']].append(self.answer_min + self.answer_max - value if item['reverse'] else value)
        scores = {}
        for key, values in groups.items():
            scores[key] = sum(values) if self.spec['aggregation'] == 'sum' else sum(values) / len(values)
        if self.spec.get('overall_mean'):
            scores['overall'] = sum(sum(v) for v in groups.values()) / len(answers)
        if difference := self.spec.get('mean_difference'):
            scores[difference['key']] = scores[difference['minuend']] - scores[difference['subtrahend']]
        return scores

    def score_ranges(self):
        """Public display bounds from the original response scale, never a new score.

        The mean-difference metric is bipolar, not a fraction of a maximum.
        """
        lo, hi = self.answer_min, self.answer_max
        totals = {key: sum(q['dimension'] == key for q in self.items)
                  for key in self.spec['dimensions']}
        ranges = {}
        for key, count in totals.items():
            if self.spec['aggregation'] == 'sum':
                ranges[key] = {'min': lo * count, 'max': hi * count, 'kind': 'sum'}
            else:
                ranges[key] = {'min': lo, 'max': hi, 'kind': 'mean'}
        if self.spec.get('overall_mean'):
            ranges['overall'] = {'min': lo, 'max': hi, 'kind': 'mean'}
        if difference := self.spec.get('mean_difference'):
            span = hi - lo
            ranges[difference['key']] = {'min': -span, 'max': span, 'kind': 'difference'}
        return ranges

    @staticmethod
    def display_score(value, bounds):
        highest, lowest = bounds['max'], bounds['min']
        if bounds['kind'] == 'difference':
            return f'{value:.2f}（范围 {lowest:g}～{highest:g}）'
        if bounds['kind'] == 'sum' and lowest != 0:
            return f'{value:.2f} / {highest:g}（范围 {lowest:g}～{highest:g}）'
        return f'{value:.2f} / {highest:g}'

    def _state(self, player, mode, session_id, answers, *, complete=False, scores=None, include_interpretation=True):
        state = {'ok': True, 'game': self.game, 'player_id': player, 'research': True,
            'mode': mode, 'edition': mode, 'session_id': session_id,
            'progress': len(answers), 'total': len(self.items), 'complete': complete,
            'instructions': self.spec['instruction'], 'notice': self.notice}
        if complete:
            ranges = self.score_ranges()
            lines = [f'【{self.title}】']
            for key, value in scores.items():
                lines.append(f'{self.result_dimensions[key]}：{self.display_score(value, ranges[key])}')
            if self.game == 'aidep':
                lines.append('研究情境：中国大学生在学习任务中使用 AI。')
            state.update(result='\n'.join(lines), result_data={'kind': 'research_scale',
                'scores': scores, 'score_ranges': ranges, 'dimensions': self.result_dimensions,
                'scoring_note': self.scoring_note, 'source': self.attribution, 'notice': self.notice})
            if include_interpretation:
                report = build_interpretation(self.spec, scores)
                if report is not None:
                    state['result_data']['interpretation'] = report
                    state['result'] += '\n\n' + format_interpretation(report)
        else:
            state['questions'] = [{**self._public_question({**q, 'options': self.spec['options']}), 'number': index + 1}
                for index, q in enumerate(self.ordered_items(session_id))]
        return state

    @staticmethod
    def _web_revision(session_id, answers):
        # A content version needs no migration and also detects MCP appends.
        return hashlib.sha256(json.dumps([session_id, answers], separators=(',', ':')).encode()).hexdigest()

    def execute(self, action, arguments, *, web=False):
        player = self._require_player_id(arguments, 'player_id')
        if action not in ('start', 'status', 'result', 'get_result', 'answer', 'answer_batch') and not (web and action == 'save_answer'):
            raise JsonRpcError(-32602, '此量表仅支持 start/status/get_result/answer/answer_batch，不提供比较。')
        now = time.time()
        with self._connect() as conn:
            self._init_db(conn)
            conn.execute('BEGIN IMMEDIATE')
            self._cleanup_expired(conn, now)
            row = conn.execute('SELECT mode, answers, created_at FROM test_sessions WHERE player_id=? AND game=?', (player, self.game)).fetchone()
            saved = conn.execute('SELECT result_detail FROM test_results WHERE player_id=? AND game=?', (player, self.game)).fetchone()
            detail = json.loads(saved[0]) if saved else None
            restart = arguments.get('restart') is True
            if action == 'start' and (restart or (row is None and detail is None)):
                mode = arguments.get('mode', arguments.get('edition'))
                if mode not in ('full', 'full_fast'):
                    raise JsonRpcError(-32602, 'mode 须为 full 或 full_fast。')
                previous_sid = str(row[2]) if row else detail['session_id'] if detail else None
                if restart and previous_sid and arguments.get('session_id') != previous_sid:
                    raise JsonRpcError(-32602, '重测须传当前 session_id，请先 status，避免重复重置。')
                if restart and (row or detail) and arguments.get('confirm') is not True:
                    raise JsonRpcError(-32602, '重测会覆盖当前进度/结果，请传 confirm=true。')
                if row is None and conn.execute('SELECT COUNT(*) FROM test_sessions').fetchone()[0] >= MAX_SESSIONS:
                    raise JsonRpcError(-32000, '当前测试人数过多，请稍后再试')
                # SQLite REAL creation timestamps are stable across restart and reads.
                # A random fractional component distinguishes even frozen-clock tests.
                created = now + secrets.randbelow(1000000) / 1000000
                conn.execute("INSERT OR REPLACE INTO test_sessions (player_id, game, mode, current_question, answers, created_at, last_active) VALUES (?, ?, ?, 0, '[]', ?, ?)", (player, self.game, mode, created, now))
                conn.execute('DELETE FROM test_results WHERE player_id=? AND game=?', (player, self.game))
                row, detail = (mode, '[]', created), None
            if row:
                mode, raw, created = row
                answers = json.loads(raw)
                sid = str(created)
                complete = False
                scores = None
            elif detail:
                mode, answers, sid = detail['mode'], detail['answers'], detail['session_id']
                complete, scores = True, detail['scores']
            else:
                raise JsonRpcError(-32003, '没有记录，请先选择模式开始。')
            duplicate = False
            if action in ('answer', 'answer_batch', 'save_answer'):
                if arguments.get('session_id') != sid:
                    raise JsonRpcError(-32602, '会话已变化，请先 status 恢复，勿重复提交旧会话。')
                if action == 'save_answer':
                    if mode != 'full':
                        raise JsonRpcError(-32602, '当前答题模式不支持逐题修改。')
                    index = arguments.get('index')
                    value = self._coerce_answer(arguments.get('answer'))
                    revision = arguments.get('expected_revision')
                    finish = arguments.get('finish', False)
                    if (type(index) is not int or not 0 <= index < len(self.items)
                            or not isinstance(revision, str) or len(revision) != 64 or type(finish) is not bool):
                        raise JsonRpcError(-32602, '答案保存参数不合法。')
                    receipt = [revision, index, value]
                    if complete:
                        # Only an exact retry of the final web request is accepted.
                        if finish and detail.get('web_finish_request') == receipt:
                            duplicate = True
                        else:
                            raise JsonRpcError(-32602, '报告已生成，不能修改本次答案。')
                    elif revision != self._web_revision(sid, answers):
                        # A repeated non-final write is harmless only if its value
                        # is already present; never apply stale edits or finalize.
                        if not finish and index < len(answers) and answers[index] == value:
                            duplicate = True
                        else:
                            raise JsonRpcError(-32602, '答案已在其他页面更新，请刷新后重试。')
                    elif index > len(answers):
                        raise JsonRpcError(-32602, '请先完成前面的题目。')
                    else:
                        if index == len(answers):
                            answers.append(value)
                        else:
                            answers[index] = value
                        if finish and (index != len(self.items) - 1 or len(answers) != len(self.items)):
                            raise JsonRpcError(-32602, '请答完全部题目后查看结果。')
                elif action == 'answer':
                    if mode != 'full':
                        raise JsonRpcError(-32602, 'full_fast 请用 answer_batch。')
                    index = arguments.get('expected_index')
                    if type(index) is not int or not 0 <= index < len(self.items):
                        raise JsonRpcError(-32602, 'expected_index 须为返回的 progress（从0开始）。')
                    value = self._coerce_answer(arguments.get('answer'))
                    if index < len(answers) and answers[index] == value:
                        duplicate = True
                    elif index != len(answers) or complete:
                        raise JsonRpcError(-32602, '答案与已提交内容冲突或题号过期，请先 status。')
                    else:
                        answers.append(value)
                else:
                    if mode != 'full_fast':
                        raise JsonRpcError(-32602, 'full 请用 answer。')
                    raw = arguments.get('answers')
                    if not isinstance(raw, list) or len(raw) != len(self.items):
                        raise JsonRpcError(-32602, f'须一次提交全部 {len(self.items)} 个答案。')
                    batch = [self._coerce_answer(value) for value in raw]
                    if complete and batch == answers:
                        duplicate = True
                    elif complete:
                        raise JsonRpcError(-32602, '已完成记录与本次答案不一致，拒绝覆盖。')
                    else:
                        answers = batch
                if not duplicate:
                    complete = len(answers) == len(self.items) and (action != 'save_answer' or finish)
                    if complete:
                        scores = self.score(answers, sid)
                        detail = {'mode': mode, 'scores': scores, 'answers': answers, 'session_id': sid}
                        if action == 'save_answer':
                            detail['web_finish_request'] = receipt
                        conn.execute('INSERT OR REPLACE INTO test_results (player_id, game, result_value, result_detail, completed_at) VALUES (?, ?, ?, ?, ?)',
                            (player, self.game, 'research', json.dumps(detail, ensure_ascii=False), now))
                        conn.execute('DELETE FROM test_sessions WHERE player_id=? AND game=?', (player, self.game))
                    else:
                        conn.execute('UPDATE test_sessions SET current_question=?, answers=?, last_active=? WHERE player_id=? AND game=?',
                            (len(answers), json.dumps(answers), now, player, self.game))
            state = self._state(player, mode, sid, answers, complete=complete, scores=scores,
                                include_interpretation=web or action != 'status')
            if duplicate:
                state['duplicate'] = True
            conn.commit()
        # The website uses the same ordered questions. MCP full only needs the current one.
        if web and not complete:
            # Owner-only web state lets the page revisit saved questions after reload.
            state['saved_answers'] = answers
            state['answer_revision'] = self._web_revision(sid, answers)
        if not complete and not web:
            if mode == 'full':
                state['questions'] = state['questions'][len(answers):len(answers)+1]
            else:
                # A shared scale needs its response labels once, not N times.
                state['options'] = self.spec['options']
                state['questions'] = [{'number': q['number'], 'text': q['text']} for q in state['questions']]
            if action in ('answer', 'answer_batch'):
                state.pop('notice', None)
                state.pop('instructions', None)
        return state
