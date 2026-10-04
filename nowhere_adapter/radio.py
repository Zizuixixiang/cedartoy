"""Bounded streaming fallback for mobile WebViews; never an arbitrary URL proxy."""
import http.client as http_client
import ipaddress
import re
import select
import socket
import ssl
import threading
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from . import storage

SLOTS = threading.BoundedSemaphore(8)
CHUNK = 32768
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 30
MAX_REDIRECTS = 3


class RadioError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def parse_url(url):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\s\\\x00-\x1f\x7f]', url):
        raise ValueError('无效的电台地址')
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError('电台地址只支持 HTTP 或 HTTPS')
    if parsed.port == 0:
        raise ValueError('无效的电台端口')
    return parsed


def canonical_url(url):
    """Compare URI-equivalent URLs, never sort/drop query arguments or decode
    reserved delimiters (%2F, %26, %3D, +). Fragments are not sent to a station.
    Match the browser's host/default-port/UTF-8/dot-segment serialization.
    """
    parsed = parse_url(url)
    original_host = parsed.hostname.lower()
    host = original_host.encode('idna').decode('ascii').lower()
    # Python's legacy IDNA codec can collapse distinct modern browser hosts
    # (e.g. ß -> ss). Reject lossy mappings rather than grant the other host.
    if not original_host.isascii() and host.encode('ascii').decode('idna') != original_host:
        raise ValueError('无效的电台域名编码')
    if ':' in host:
        host = '[' + ipaddress.IPv6Address(host).compressed + ']'
    port = parsed.port
    if port is not None and port != (443 if parsed.scheme == 'https' else 80):
        host += ':' + str(port)
    unreserved = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
    def component(value, safe):
        if re.search(r'%(?![0-9a-fA-F]{2})', value):
            raise ValueError('无效的电台地址编码')
        value = quote(value, safe=safe + '%')
        def percent(match):
            char = chr(int(match[1], 16))
            return char if char in unreserved else '%' + match[1].upper()
        return re.sub(r'%([0-9a-fA-F]{2})', percent, value)
    path = component(parsed.path or '/', "/:@!$&'()*+,;=-._~")
    # Remove only complete dot segments; preserve repeated slashes and case.
    segments = []
    parts = path.split('/')
    for i, part in enumerate(parts):
        if part == '..':
            if len(segments) > 1:
                segments.pop()
            if i == len(parts) - 1:
                segments.append('')
        elif part == '.':
            if i == len(parts) - 1:
                segments.append('')
        else:
            segments.append(part)
    path = '/'.join(segments) or '/'
    query = component(parsed.query, '/?:@!$&()*+,;=-._~')
    result = urlunsplit((parsed.scheme, host, path, query, ''))
    if not query and '?' in url.split('#', 1)[0]:
        result += '?'
    return result


def persisted_streams(saved):
    """Only upstream's explicit radio fields, never a recursive URL search.

    Footprints are slot-wide. WorldState also persists the station selected for
    each journey BEFORE listen records a footprint. /state exposes last_env.radio;
    inactive journeys are the WorldState files referenced by journeys/index.json.
    """
    files = (saved or {}).get('files', {})
    for item in files.get('footprints.json', {}).get('items', []):
        if isinstance(item, dict):
            yield item.get('stream_url')
    states = [files.get('journey.json', {})]
    for entry in files.get('journeys/index.json', {}).get('journeys', []):
        states.append(files.get('journeys/' + entry['slug'] + '.json', {}))
    for state in states:
        env = state.get('last_env') or {}
        for station in (state.get('radio_station'), env.get('radio') if isinstance(env, dict) else None):
            if isinstance(station, dict):
                yield station.get('stream_url')


def authorize(player, url):
    requested = canonical_url(url)
    with storage.locked(player):
        candidates = list(persisted_streams(storage.read(player)))
    for value in candidates:
        if not isinstance(value, str):
            continue
        try:
            candidate = canonical_url(value)
        except ValueError:
            continue  # Malformed historical entries grant no proxy permission.
        if candidate == requested:
            return candidate
    raise RadioError('电台不在当前旅程足迹中', 403)


def public_addresses(host, port):
    if host.rstrip('.').lower() == 'localhost' or host.rstrip('.').lower().endswith('.localhost'):
        raise RadioError('电台地址不可用', 403)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise RadioError('电台地址不可用')
    for _family, _kind, _proto, _canon, address in addresses:
        ip = ipaddress.ip_address(address[0].split('%', 1)[0])
        if (not ip.is_global or ip.is_multicast or ip.is_reserved
                or getattr(ip, 'is_site_local', False)
                or any(getattr(ip, name, None) is not None for name in ('ipv4_mapped', 'sixtofour', 'teredo'))
                # Python 3.10's special-purpose IPv4 tables predate these ranges.
                or (ip.version == 4 and any(ip in ipaddress.ip_network(net) for net in
                                            ('192.0.0.0/24', '192.88.99.0/24')))):
            raise RadioError('电台地址不可用', 403)
    return addresses


class PinnedConnection(http_client.HTTPConnection):
    """Connect to the validated numeric sockaddr, with original TLS SNI/Host."""
    def __init__(self, parsed):
        super().__init__(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80),
                         timeout=CONNECT_TIMEOUT)
        self.tls = parsed.scheme == 'https'

    def connect(self):
        addresses = public_addresses(self.host, self.port)
        # One pinned address per attempt; never resolve the hostname again at dial.
        family, kind, proto, _, address = addresses[0]
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect(address)
            if self.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            sock.settimeout(READ_TIMEOUT)
            self.sock = sock
        except BaseException:
            sock.close()
            raise


def open_stream(url):
    for redirect in range(MAX_REDIRECTS + 1):
        parsed = parse_url(url)
        conn = PinnedConnection(parsed)
        try:
            target = parsed.path or '/'
            if parsed.query:
                target += '?' + parsed.query
            conn.request('GET', target, headers={'Accept': 'audio/*, application/octet-stream',
                                                'Accept-Encoding': 'identity', 'Connection': 'close'})
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                response.close()
                if not location or redirect == MAX_REDIRECTS:
                    raise RadioError('电台重定向不可用')
                url = urljoin(url, location)
                conn.close()
                continue  # Revalidate scheme, all DNS answers and pinned IP at EVERY hop.
            mime = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
            if (response.status != 200 or not (re.fullmatch(r'audio/[a-z0-9!#$&^_.+-]+', mime) or mime == 'application/octet-stream')
                    or response.getheader('Content-Encoding', 'identity').lower() != 'identity'):
                response.close()
                raise RadioError('电台未返回音频流')
            return conn, response, mime
        except BaseException:
            conn.close()
            raise
    raise RadioError('电台重定向不可用')


def stream(http, player, url):
    url = authorize(player, url)
    # Small VPS fallback: at most 8 readers + 8 disconnect watchers, no worker
    # pool changes, transcoding, cache files or unbounded memory buffering.
    if not SLOTS.acquire(blocking=False):
        raise RadioError('电台连接已满，请稍后重试', 503)
    conn = response = watcher = None
    stopped = threading.Event()
    started = False
    try:
        conn, response, mime = open_stream(url)
        # HTTPConnection drops its reference on Connection: close responses.
        upstream = response.fp.raw._sock
        def watch_disconnect():
            client = http.connection
            while not stopped.is_set():
                try:
                    ready, _, _ = select.select([client], [], [], .2)
                    if ready:
                        # This is a dedicated closing stream connection. EOF or
                        # any pipelined input ends it; never consume another API.
                        if isinstance(client, ssl.SSLSocket):
                            client.recv(1)
                        else:
                            client.recv(1, socket.MSG_PEEK)
                        stopped.set()
                        upstream.shutdown(socket.SHUT_RDWR)
                        return
                except (OSError, ValueError):
                    return
        http.close_connection = True
        http.connection.settimeout(READ_TIMEOUT)  # Also bound slow downstream writes.
        watcher = threading.Thread(target=watch_disconnect, daemon=True)
        watcher.start()
        http.send_response(200)
        http.send_header('Content-Type', mime)
        http.send_header('Cache-Control', 'no-store')
        http.send_header('X-Content-Type-Options', 'nosniff')
        http.send_header('X-Accel-Buffering', 'no')
        http.send_header('Connection', 'close')
        # Close-delimited response works with HTTP/1.0 and HTTP/1.1; read1 also
        # decodes an upstream chunked stream incrementally, never read-all.
        for name in ('icy-name', 'icy-genre', 'icy-br', 'icy-metaint'):
            value = response.getheader(name)
            if value and len(value) <= 512 and not re.search(r'[\x00-\x1f\x7f]', value):
                http.send_header(name, value)
        http.end_headers()
        started = True
        while not stopped.is_set():
            data = response.read1(CHUNK)
            if not data:
                break
            http.wfile.write(data)
            http.wfile.flush()
    except (OSError, http_client.HTTPException):
        if not started:
            raise RadioError('电台暂时无法连接') from None
        # Headers already sent: terminate the stream, never append JSON to audio.
    finally:
        stopped.set()
        if response is not None:
            response.close()
        if conn is not None:
            conn.close()
        if watcher is not None:
            try:
                http.connection.shutdown(socket.SHUT_RD)
            except OSError:
                pass
            watcher.join(timeout=.5)
        SLOTS.release()
