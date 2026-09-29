"""Cabeceras de seguridad, con `/docs` exento de la política de contenido.

Convención de todos los servicios (CLAUDE.md, «Convenciones de Express»), aquí
sin `helmet`: se escriben las mismas cabeceras que `helmet` emite por omisión en
`pricing`, comprobadas contra su salida.

`/docs` sale SIN `Content-Security-Policy`. Con ella, Swagger UI no puede cargar
sus recursos de la CDN ni hablar con la pasarela, que es otro origen, y cada «Try
it out» muere dentro del navegador con un «Failed to fetch» que culpa a CORS sin
que CORS tenga nada que ver. Las demás cabeceras se conservan también en `/docs`,
y la exención se acota a esa ruta: hay prueba en los dos sentidos.

Aquí sí es middleware, y no contradice a `src/core/identity.py`: esto decora las
respuestas, no rechaza peticiones, así que no emite cuerpo de error alguno.
"""

CSP = (
    "default-src 'self';base-uri 'self';font-src 'self' https: data:;"
    "form-action 'self';frame-ancestors 'self';img-src 'self' data:;"
    "object-src 'none';script-src 'self';script-src-attr 'none';"
    "style-src 'self' https: 'unsafe-inline';upgrade-insecure-requests"
)

HEADERS = [
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"origin-agent-cluster", b"?1"),
    (b"referrer-policy", b"no-referrer"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-dns-prefetch-control", b"off"),
    (b"x-download-options", b"noopen"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"x-permitted-cross-domain-policies", b"none"),
    (b"x-xss-protection", b"0"),
]

EXEMPT_FROM_CSP = "/docs"


class SecurityHeadersMiddleware:
    """Middleware ASGI puro: no envuelve el cuerpo ni altera el flujo de errores."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        exempt = path == EXEMPT_FROM_CSP or path.startswith(EXEMPT_FROM_CSP + "/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name for name, _ in headers}
                extra = (
                    HEADERS if exempt else [(b"content-security-policy", CSP.encode()), *HEADERS]
                )
                headers.extend((n, v) for n, v in extra if n not in present)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
