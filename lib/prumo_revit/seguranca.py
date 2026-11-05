# -*- coding: utf-8 -*-
"""Autorização das rotas /prumo/*.

O agente Prumo cria um token aleatório em %LOCALAPPDATA%\\Prumo\\local_token (perfil do
usuário do Windows) e manda em todo pedido no cabeçalho X-Prumo-Token. Pedidos de navegador
(com Origin) e callbacks são recusados: um site aberto no computador não consegue usar as rotas.
"""
import os

from pyrevit import routes


def _token_path():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Prumo", "local_token")


def _token():
    try:
        with open(_token_path(), "r") as f:
            t = f.read().strip()
        return t if len(t) >= 32 else None
    except Exception:
        return None


def _header(headers, name):
    if headers is None:
        return None
    try:
        for k in headers.keys():
            if str(k).lower() == name:
                return headers[k]
    except Exception:
        pass
    return None


def _same(a, b):
    """Comparação em tempo constante."""
    if a is None or b is None or len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b):
        r |= ord(x) ^ ord(y)
    return r == 0


def negar(request):
    """Devolve a resposta de recusa, ou None quando o pedido pode seguir."""
    if isinstance(request.data, dict) and "callbackUrl" in request.data:
        request.data.pop("callbackUrl", None)
        return routes.make_response(data={"error": "Callbacks não são aceitos."}, status=400)
    if _header(request.headers, "origin"):
        return routes.make_response(data={"error": "Pedidos de navegador não são aceitos."}, status=403)
    esperado = _token()
    if not esperado:
        return routes.make_response(data={"error": "Agente Prumo não configurado neste computador."}, status=401)
    if not _same(str(_header(request.headers, "x-prumo-token") or ""), esperado):
        return routes.make_response(data={"error": "Token local inválido."}, status=401)
    return None
