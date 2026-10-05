"""HTTP shell helpers, with dependencies supplied by server at call time.

The compatibility methods retain their signatures and all per-game handler and
proxy boundaries. Route order and response bytes are preserved.
"""


def init_thread_pool(
    self,
    server_address,
    RequestHandlerClass,
    max_workers,
    *,
    BoundedSemaphore,
    ThreadPoolExecutor,
    base_init,
):
    self.executor = ThreadPoolExecutor(max_workers=max_workers)
    self.worker_slots = BoundedSemaphore(max_workers)
    base_init(server_address, RequestHandlerClass)


def process_request(self, request, client_address, *, QUEUE_TIMEOUT_SECONDS):
    if not self.worker_slots.acquire(timeout=QUEUE_TIMEOUT_SECONDS):
        self._send_busy(request)
        self.close_request(request)
        return
    self.executor.submit(self._process_request_thread, request, client_address)


def process_request_thread(self, request, client_address):
    try:
        self.finish_request(request, client_address)
    except Exception:
        self.handle_error(request, client_address)
    finally:
        self.shutdown_request(request)
        self.worker_slots.release()


def close_thread_pool(self, *, base_close):
    base_close()
    if hasattr(self, "executor"):
        self.executor.shutdown(wait=True)


def send_busy(request):
    body = b'{"error":"server busy"}'
    response = (
        b"HTTP/1.1 503 Service Unavailable\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"Connection: close\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        b"\r\n" + body
    )
    try:
        request.sendall(response)
    except OSError:
        pass


def main(
    *,
    CedarToyHandler,
    HOST,
    MAX_WORKERS,
    PORT,
    TURTLE_DB_PATH,
    Thread,
    ThreadPoolHTTPServer,
    _init_announcement_tables,
    _migrate_platform_timestamps,
    avatar_appearances,
    time,
):
    _migrate_platform_timestamps()
    # Reconcile after the Beijing-time migration; ownership survives every restart.
    avatar_appearances.reconcile_one_w(TURTLE_DB_PATH)
    if time.time() < avatar_appearances.ONE_W_END_EPOCH:
        Thread(
            target=avatar_appearances.watch_one_w,
            args=(TURTLE_DB_PATH,), name="avatar-1w-catchup", daemon=True,
        ).start()
    _init_announcement_tables()
    server = ThreadPoolHTTPServer((HOST, PORT), CedarToyHandler)
    print(f"CedarToy listening on {HOST}:{PORT} with max_workers={MAX_WORKERS}")
    server.serve_forever()
