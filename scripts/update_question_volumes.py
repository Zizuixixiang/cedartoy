#!/usr/bin/env python3
"""Offline precomputation. --write updates metadata/static Guide lines; --check verifies both.

Count each question stem once and each unique option set only once.
Use Unicode len and cl100k_base.encode on each text, then sum.
No JSON, numbering, option values, instructions, answers or tool overhead included.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cedar_backend.question_volume import METADATA_PATH, format_volume
from deeptalk.catalog import ALIASES, load_topic

LEGACY_MODES = {
    'mbti': ('short', 'full'), 'enneagram': ('quick', 'full'),
    'dnd': ('full',), 'love': ('full',), 'ecr': ('full',),
    'humanity': ('full',), 'sins_virtues': ('full',),
}
GUIDES = ROOT / 'turtle-soup/backend/guides'


def build_metadata():
    import tiktoken  # Only this offline generator/test requires tiktoken.
    encoder = tiktoken.get_encoding('cl100k_base')

    def measure(texts):
        return {'chars': sum(map(len, texts)),
                'tokens': sum(len(encoder.encode(t)) for t in texts)}

    def variant(questions, mode='', choices=None):
        stems = [q['text'] for q in questions]
        texts = list(stems)
        unique_sets = []
        seen = set()
        for options in choices or ():
            signature = tuple(options)
            if signature not in seen:
                texts.extend(options)
                unique_sets.append(options)
                seen.add(signature)
        fingerprint = {'stems': stems, 'unique_option_sets': unique_sets}
        return {'mode': mode, 'count': len(stems), **measure(texts),
                'text_sha256': hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False,
                                                         separators=(',', ':')).encode()).hexdigest()}

    games = {game: {'variants': [variant(load_topic(tid)['questions'])]} for game, tid in ALIASES.items()}
    for path in sorted((ROOT / 'research_scales/data').glob('*.json')):
        spec = json.loads(path.read_text(encoding='utf-8'))
        if not spec.get('human_only') and not spec.get('blocked'):
            games[spec['game']] = {'variants': [variant(spec['items'], choices=[[o['text'] for o in spec['options']]])]}
    for game, modes in LEGACY_MODES.items():
        questions = importlib.import_module(game + '.questions')
        games[game] = {'variants': [variant(questions.get_questions(mode), mode + '/' + mode + '_fast', choices=[
                                               [q['option_a'],q['option_b']] if 'option_a' in q else
                                               [o['text'] for o in q['options']]
                                               for q in questions.get_questions(mode)]) for mode in modes]}
    games['bdsmtest'] = {'unavailable': '原站实时出题，无稳定本地题包；不据93题提示推算'}
    return {'encoding': 'cl100k_base', 'tiktoken_version': tiktoken.__version__,
            'method': 'Unicode len and per-text cl100k_base; each question stem once, each unique option set once',
            'games': games}


def static_guide(text, line):
    lines = [s for s in text.splitlines() if not s.startswith(('题文体积：','整套题库文本：'))]
    lines[1:1] = ['', line]
    return '\n'.join(lines).rstrip() + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.write == args.check:
        parser.error('choose exactly one of --write or --check')
    data = build_metadata()
    content = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if args.write:
        METADATA_PATH.write_text(content, encoding='utf-8')
    elif not METADATA_PATH.exists() or METADATA_PATH.read_text(encoding='utf-8') != content:
        raise SystemExit('Stale question volume metadata: run --write and review the diff.')
    for game in (*LEGACY_MODES, 'bdsmtest'):
        path = GUIDES / (game + '.md')
        text = path.read_text(encoding='utf-8')
        line = format_volume(data['games'][game])
        if args.write:
            # Preserve the existing Guide text; replace just the one metadata line.
            if any(s.startswith(('题文体积：','整套题库文本：')) for s in text.splitlines()):
                text = '\n'.join(line if s.startswith(('题文体积：','整套题库文本：')) else s for s in text.splitlines()) + '\n'
            else:
                text = static_guide(text, line)
            path.write_text(text, encoding='utf-8')
        elif [s for s in text.splitlines() if s.startswith(('题文体积：','整套题库文本：'))] != [line]:
            raise SystemExit(f'Stale volume line: {path.relative_to(ROOT)}')
    print(f'{len(data["games"])} Guide entries verified; per-question cl100k_base, no network calls.')


if __name__ == '__main__':
    main()
