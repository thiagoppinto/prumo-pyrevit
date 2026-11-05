# -*- coding: utf-8 -*-
"""Executor de ROTEIROS: código do Revit escrito pelo assistente, rodando com travas.

Fluxo (o hub só deixa aplicar o que foi simulado antes, pelo hash):
  simular -> valida o código (seguranca_codigo.py), roda numa transação dentro de um TransactionGroup, mede o que mudou
             (adicionados/modificados/removidos, por categoria) e DESFAZ tudo (group.RollBack());
  aplicar -> mesmo código; se removeu elementos ou mexeu em coisas demais e não veio confirm -> desfaz e devolve 428;
             senão grava como UM passo de "Desfazer" do Revit (group.Assimilate()).
Travas: flag escrita_habilitada (cópia liberada, 403 sem ela) para simular E aplicar; nunca salva nem sincroniza;
builtins restritos e __import__ próprio (lista fechada); só `DB`, `doc`, `List`, `math`, `re` no ambiente.
Limite conhecido: laços aninhados enormes sobre milhares de elementos podem demorar (não há como interromper o Revit).
Compatível com IronPython 2.7/3.4 e CPython 3 (sem f-strings).
"""
import json
import math
import re
import sys
import time
import traceback

from pyrevit import DB
from System import EventHandler
from System.Collections.Generic import List
from Autodesk.Revit.DB.Events import DocumentChangedEventArgs

from . import seguranca_codigo as sc
from .escrita import Recusa, _permitido
from .leitura import _id, _nome

try:
    import builtins as _bi
except ImportError:  # IronPython 2.7
    import __builtin__ as _bi

MAX_ALTERACOES = 2000      # acima disso, só aplica com confirm
MAX_SAIDA = 4000
MAX_RESULTADO = 20000
MAX_RANGE = 200000
MAX_EXEMPLOS = 15

_BUILTINS_OK = (
    "abs all any bool dict enumerate filter float frozenset hasattr int isinstance issubclass iter len list map max min "
    "next pow reversed round set slice sorted str sum tuple type zip repr chr ord divmod hex oct bin format callable id hash "
    "unicode long basestring unichr cmp "
    "True False None Exception ValueError TypeError KeyError IndexError AttributeError RuntimeError ZeroDivisionError "
    "StopIteration NotImplementedError ArithmeticError LookupError OverflowError ImportError NameError"
).split()


class _Saida(object):
    """Captura print (e 'print x' do Python 2) sem estourar memória."""
    def __init__(self):
        self.partes = []
        self.n = 0

    def write(self, s):
        if self.n < MAX_SAIDA:
            try:
                s = s if isinstance(s, sc.TEXTO) else str(s)
            except Exception:
                s = "?"
            self.partes.append(s)
            self.n += len(s)

    def flush(self):
        pass

    def texto(self):
        return "".join(self.partes)[:MAX_SAIDA]


def _range_seguro(*a):
    base = getattr(_bi, "xrange", None) or _bi.range
    try:
        r = base(*a)
        if len(r) > MAX_RANGE:
            raise ValueError("range grande demais (máximo {} itens)".format(MAX_RANGE))
        return r
    except OverflowError:
        raise ValueError("range grande demais (máximo {} itens)".format(MAX_RANGE))


def _importar(nome, globais=None, locais=None, fromlist=(), level=0):
    if level != 0 or nome not in sc.MODULOS_OK:
        raise ImportError("import não permitido: {}".format(nome))
    return _bi.__import__(nome, globais, locais, fromlist, level)


def _ambiente(doc, saida):
    b = {}
    for n in _BUILTINS_OK:
        if hasattr(_bi, n):
            b[n] = getattr(_bi, n)
    b["range"] = _range_seguro
    b["xrange"] = _range_seguro
    b["__import__"] = _importar
    b["print"] = lambda *a, **k: saida.write(" ".join(str(x) for x in a) + "\n")
    return {"__builtins__": b, "__name__": "roteiro_prumo", "DB": DB, "doc": doc, "List": List, "math": math, "re": re, "resultado": None}


def _linha_do_erro():
    """Número da linha do roteiro onde a exceção ocorreu (None se não achar)."""
    tb = sys.exc_info()[2]
    linha = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<roteiro>":
            linha = tb.tb_lineno
        tb = tb.tb_next
    return linha


def _resumir(doc, add, mod, dele):
    por_cat, exemplos = {}, []
    for tipo, d in (("adicionado", add), ("modificado", mod)):
        for i, (eid) in enumerate(list(d.values())):
            try:
                el = doc.GetElement(eid)
                cat = el.Category.Name if el is not None and el.Category is not None else "(sem categoria)"
            except Exception:
                el, cat = None, "(sem categoria)"
            chave = tipo + "s: " + cat
            por_cat[chave] = por_cat.get(chave, 0) + 1
            if len(exemplos) < MAX_EXEMPLOS and el is not None:
                try:
                    nome = _nome(el)
                except Exception:
                    nome = ""
                exemplos.append({"tipo": tipo, "id": _id(eid), "categoria": cat, "nome": nome})
            if i >= 1500:  # não gastar minutos classificando milhares de elementos
                por_cat[chave + " (amostra)"] = True
                break
    return {"adicionados": len(add), "modificados": len(mod), "removidos": len(dele), "por_categoria": por_cat, "exemplos": exemplos}


def executar(doc, codigo, modo, plano_id, confirm):
    """modo: 'simular' (desfaz tudo) ou 'aplicar'. Levanta Recusa (403/428), ValueError (código recusado/erro do roteiro)."""
    if modo not in ("simular", "aplicar"):
        raise ValueError("mode deve ser simular ou aplicar.")
    _permitido(doc)  # 403 sem a flag da cópia liberada (vale também para simular)
    problemas = sc.validar(codigo)
    if problemas:
        raise ValueError("Código recusado: " + "; ".join(problemas))
    plano_id = str(plano_id or "").strip()
    if not plano_id:
        raise ValueError("plan_id obrigatório.")

    saida = _Saida()
    ns = _ambiente(doc, saida)
    add, mod, dele = {}, {}, {}

    def _evento(sender, args):
        try:
            if not args.GetDocument().Equals(doc):
                return
            for i in args.GetAddedElementIds():
                add[_id(i)] = i
            for i in args.GetModifiedElementIds():
                mod[_id(i)] = i
            for i in args.GetDeletedElementIds():
                dele[_id(i)] = i
        except Exception:
            pass

    app = doc.Application
    handler = EventHandler[DocumentChangedEventArgs](_evento)
    nome = "Prumo | roteiro " + plano_id[:30]
    grupo = DB.TransactionGroup(doc, nome)
    t = DB.Transaction(doc, nome)
    stdout_antigo = sys.stdout
    t0 = time.time()
    resultado, erro_exec = None, None
    grupo.Start()
    app.DocumentChanged += handler
    try:
        t.Start()
        try:
            fho = t.GetFailureHandlingOptions()
            fho.SetForcedModalHandling(False)
            fho.SetClearAfterRollback(True)
            t.SetFailureHandlingOptions(fho)
        except Exception:
            pass
        sys.stdout = saida
        try:
            exec(compile(codigo, "<roteiro>", "exec"), ns)
        except Exception as e:
            erro_exec = "Erro na linha {}: {}: {}".format(_linha_do_erro() or "?", type(e).__name__, str(e)[:300])
        finally:
            sys.stdout = stdout_antigo
        if erro_exec is not None:
            if t.HasStarted():
                t.RollBack()
            grupo.RollBack()
            raise ValueError(erro_exec + (" | saída: " + saida.texto()[:500] if saida.texto() else ""))
        st = t.Commit()
        if str(st) != "Committed":
            grupo.RollBack()
            raise ValueError("O Revit recusou as alterações do roteiro (" + str(st) + "). Nada foi gravado.")
        resumo = _resumir(doc, add, mod, dele)
        total = resumo["adicionados"] + resumo["modificados"] + resumo["removidos"]
        if modo == "aplicar" and not confirm and (resumo["removidos"] > 0 or total > MAX_ALTERACOES):
            grupo.RollBack()
            raise Recusa("O roteiro remove {} elemento(s) ou altera {} no total (limite sem confirmação: {}). Confirme com o usuário e reenvie com confirm=true. Nada foi gravado.".format(resumo["removidos"], total, MAX_ALTERACOES), 428)
        if modo == "simular":
            grupo.RollBack()
        else:
            grupo.Assimilate()
        resultado = ns.get("resultado")
    except Exception:
        # qualquer falha: nada fica gravado
        try:
            if t.HasStarted():
                t.RollBack()
        except Exception:
            pass
        try:
            if grupo.HasStarted():
                grupo.RollBack()
        except Exception:
            pass
        raise
    finally:
        sys.stdout = stdout_antigo
        try:
            app.DocumentChanged -= handler
        except Exception:
            pass

    try:
        res_txt = json.dumps(resultado, default=str, ensure_ascii=False)
    except Exception:
        res_txt = json.dumps(str(resultado))
    if len(res_txt) > MAX_RESULTADO:
        res_json = {"truncado": res_txt[:MAX_RESULTADO]}
    else:
        res_json = json.loads(res_txt)
    return {"ok": True, "mode": modo, "plan_id": plano_id, "resumo": resumo, "saida": saida.texto(), "resultado": res_json,
            "tempo_s": round(time.time() - t0, 1),
            "avisos": ["Simulação: tudo foi desfeito, nada foi gravado."] if modo == "simular" else ["Aplicado como um único passo de Desfazer (Ctrl+Z) no Revit. Nada foi salvo em disco."]}
