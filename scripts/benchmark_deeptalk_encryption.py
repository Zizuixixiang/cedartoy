#!/usr/bin/env python3
"""Local simulation only: ephemeral key and SQLite DB, no configured data access."""
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deeptalk.crypto import Cipher, KEY_ENV
from deeptalk.store import Store
from deeptalk.catalog import load_topic


def measure(fn, count):
    samples = []
    for _ in range(count):
        start = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - start) / 1000)
    return dict(mean_us=round(statistics.mean(samples), 2),
                p95_us=round(sorted(samples)[int(count * .95)], 2), iterations=count)


def main():
    with tempfile.TemporaryDirectory(prefix='deeptalk-crypto-bench-') as temp:
        key_file = Path(temp) / 'test.key'
        key_file.write_bytes(os.urandom(32)); key_file.chmod(0o600)
        with patch.dict(os.environ, {KEY_ENV: str(key_file)}):
            cipher = Cipher()
            results = []
            context = ['benchmark-only']
            for size in (128, 8000, 800000):
                value = 'x' * size
                encrypted = cipher.encrypt(value, context)
                assert cipher.decrypt(encrypted, context) == value
                results.append(dict(plaintext_bytes=size, stored_bytes=len(encrypted),
                                    encrypt=measure(lambda: cipher.encrypt(value, context), 200),
                                    decrypt=measure(lambda: cipher.decrypt(encrypted, context), 200)))
            db = Path(temp) / 'test.db'
            human, ai = dict(id=1, username='test'), dict(id=2, username='test-ai', is_ai=True)
            store = Store(db)
            rid = store.create(human, ai, 'relations_communication', 'benchmark_create')['round_id']
            qid = load_topic('relations_communication')['questions'][0]['id']
            revision = 0

            def write():
                nonlocal revision
                # Fresh Store matches a real service request, including key loading.
                Store(db).answer(human, dict(round_id=rid, question_id=qid, revision=revision,
                                            request_id=f'benchmark_{revision}', text='x' * 8000))
                revision += 1

            end_to_end = dict(answer_8000_bytes=measure(write, 100),
                              question_8000_bytes=measure(lambda: Store(db).read(human, rid, action='question'), 100))
            print(json.dumps(dict(cipher=results, sqlite=end_to_end), indent=2))


if __name__ == '__main__':
    main()
