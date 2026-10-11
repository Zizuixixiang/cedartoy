"""Precomputed Guide-only text sizes; tokenizer is a development dependency only."""
from functools import lru_cache
import json
from pathlib import Path

METADATA_PATH = Path(__file__).with_name('question_volumes.json')


@lru_cache(maxsize=1)
def metadata():
    return json.loads(METADATA_PATH.read_text(encoding='utf-8'))


def format_volume(entry):
    if 'unavailable' in entry:
        return '整套题库文本：原站实时出题，题干与选项 Token 总量未测；不能由通常93题推算。'
    parts = []
    for item in entry['variants']:
        prefix = f"{item['mode']} " if item.get('mode') else ''
        parts.append(f"{prefix}{item['count']}题 {item['chars']}字符 / 约{item['tokens']} Token")
    return '整套题库文本：' + '；'.join(parts) + '（题干逐题计，相同选项组仅计一次；cl100k_base，非实际调用总消耗）。'


def guide_volume(game):
    return format_volume(metadata()['games'][game])
