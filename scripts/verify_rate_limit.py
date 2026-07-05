import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


def main():
    identity = "ip:203.0.113.9"
    with server._RATE_LIMIT_LOCK:
        server._REQUEST_RATE_LIMIT.clear()

    for _ in range(server.REQUEST_RATE_LIMIT_MAX):
        assert server._check_request_rate_limit(identity)

    assert not server._check_request_rate_limit(identity)
    response = server._json_rpc_error(
        "verify-rate-limit",
        server.RATE_LIMIT_ERROR_CODE,
        server.REQUEST_RATE_LIMIT_MESSAGE,
    )
    assert response["error"]["message"] == server.REQUEST_RATE_LIMIT_MESSAGE
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
