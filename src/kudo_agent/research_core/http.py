import asyncio
import hashlib
import ipaddress
import socket
from collections import defaultdict
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urljoin, urlsplit

import httpx

from kudo_agent.evidence.store import Store
from kudo_agent.research_core.models import Artifact, Budget, RequestSpec, digest, timestamp


class QueryConnectionError(RuntimeError):
    def __init__(self, method: str, url: str, cause: BaseException):
        details, seen = [], set()
        current = cause
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            detail = f"{type(current).__name__}: {str(current).strip() or '连接未完成'}"
            if detail not in details:
                details.append(detail)
            current = current.__cause__ or current.__context__
        super().__init__(f"{method} {url}；" + "；".join(details))


class QueryTargetBlocked(ValueError):
    pass


class HttpCookiePolicy(DefaultCookiePolicy):
    def __init__(self):
        super().__init__()
        self.rejected: dict[str, str] = {}

    def set_ok(self, cookie, request):
        if (not cookie.name.isascii() or not cookie.name.isprintable()
                or (cookie.value and (not cookie.value.isascii() or not cookie.value.isprintable()))):
            url = request.get_full_url()
            self.rejected[url] = f"站点 Cookie 包含请求头不支持的字符，已终止查询：{url}"
            return False
        return super().set_ok(cookie, request)


async def validate_public_url(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("采集地址必须是无登录凭据的 HTTP 或 HTTPS 地址")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443}:
        raise QueryTargetBlocked(f"公开资料采集仅允许 80 和 443 端口：{url}")
    addresses = await asyncio.get_running_loop().getaddrinfo(
        parsed.hostname, port, type=socket.SOCK_STREAM
    )
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise QueryTargetBlocked(f"采集地址解析到了非公共网络，已拒绝访问：{url}")


class HttpExecutor:
    def __init__(self, store: Store, run_id: str, budget: Budget):
        self.store, self.run_id, self.budget = store, run_id, budget
        self.global_limit = asyncio.Semaphore(budget.concurrency)
        self.domains = defaultdict(lambda: asyncio.Semaphore(budget.per_domain))
        self.locks = defaultdict(asyncio.Lock)
        self.clients: dict[str, httpx.AsyncClient] = {}
        self.cookie_policies: dict[str, HttpCookiePolicy] = {}

    async def close(self):
        for client in self.clients.values():
            await client.aclose()

    async def fetch(self, spec: RequestSpec) -> Artifact:
        try:
            return await self._fetch(spec)
        except (httpx.NetworkError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.WriteTimeout, socket.gaierror, TimeoutError) as error:
            url = str(error.request.url) if isinstance(error, httpx.RequestError) else spec.url
            raise QueryConnectionError(spec.method, url, error) from error
        except (httpx.RemoteProtocolError, httpx.DecodingError) as error:
            raise QueryTargetBlocked(
                f"站点返回无法处理的 HTTP 响应：{error.request.url}；{type(error).__name__}: {error}"
            ) from error

    async def _fetch(self, spec: RequestSpec) -> Artifact:
        key = digest(spec.model_dump(mode="json"))
        async with self.locks[key]:
            saved = self.store.artifact(self.run_id, key)
            if saved:
                self.store.body(self.run_id, saved)
                return saved
            if spec.identity_scope not in self.clients:
                policy = HttpCookiePolicy()
                self.cookie_policies[spec.identity_scope] = policy
                self.clients[spec.identity_scope] = httpx.AsyncClient(
                    timeout=self.budget.request_timeout, follow_redirects=False,
                    cookies=CookieJar(policy=policy),
                    headers={"User-Agent": "KudoAgent/0.1 (+public-footprint-research)"},
                )
            client = self.clients[spec.identity_scope]
            request = client.build_request(
                spec.method, spec.url, headers=spec.headers, params=spec.params,
                data=spec.form, json=spec.json_body,
                files={key: (None, value) for key, value in spec.multipart.items()} if spec.multipart is not None else None,
            )
            history = []
            async with asyncio.timeout(self.budget.request_timeout):
                for hop in range(6):
                    await validate_public_url(str(request.url))
                    domain = request.url.host
                    if self.store.run(self.run_id)["cancel_requested"]:
                        raise asyncio.CancelledError()
                    hop_key = key if hop == 0 else digest([key, hop, str(request.url)])
                    async with self.global_limit, self.domains[domain]:
                        self.store.reserve_request(self.run_id, hop_key, spec.account_request, self.budget.max_requests)
                        response = await client.send(request, stream=True)
                        try:
                            rejected = self.cookie_policies[spec.identity_scope].rejected.pop(str(request.url), None)
                            if rejected:
                                raise QueryTargetBlocked(rejected)
                            chunks, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > self.budget.max_response_bytes:
                                    raise QueryTargetBlocked(f"响应内容超过允许大小，已终止读取：{request.url}")
                                chunks.append(chunk)
                            body = b"".join(chunks)
                        finally:
                            await response.aclose()
                    if hop:
                        with self.store.db:
                            self.store.db.execute(
                                "UPDATE request_intents SET completed=1 WHERE run_id=? AND request_key=?",
                                (self.run_id, hop_key),
                            )
                    if spec.follow_redirects and response.is_redirect and response.headers.get("location"):
                        history.append({"url": str(request.url), "status": response.status_code,
                                        "location": response.headers["location"]})
                        destination = urljoin(str(request.url), response.headers["location"])
                        method = "GET" if response.status_code == 303 else request.method
                        if request.method == "POST" and response.status_code in {301, 302}:
                            method = "GET"
                        if method == "POST":
                            raise QueryTargetBlocked(f"账户 POST 请求遇到保持请求体的跳转，已终止：{spec.url}")
                        request = client.build_request(method, destination)
                        continue
                    artifact = Artifact(
                        artifact_id=key, request_key=key, method=spec.method, url=spec.url,
                        final_url=str(response.url), status_code=response.status_code,
                        headers={k: v for k, v in response.headers.items()
                                 if k.lower() in {"content-type", "location", "retry-after", "etag", "last-modified"}},
                        observed_at=timestamp(), sha256=hashlib.sha256(body).hexdigest(),
                        body_path=f"artifacts/{key}.bin", encoding=response.encoding or "utf-8",
                        redirects=history,
                    )
                    self.store.save_artifact(self.run_id, artifact, body)
                    return artifact
            raise QueryTargetBlocked(f"页面跳转超过 5 次，已终止查询：{spec.url}")

    def text(self, artifact: Artifact) -> str:
        return self.store.body(self.run_id, artifact).decode(artifact.encoding, errors="replace")
