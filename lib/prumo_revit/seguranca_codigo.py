# -*- coding: utf-8 -*-
"""Validador do código gerado pelo assistente (módulo PURO: não importa o Revit, é testável em qualquer Python).

Política: o roteiro só enxerga a API do Revit (DB) e o documento. Nada de arquivos, rede, processos, reflexão,
transações próprias (quem abre/fecha a transação é o executor), laços sem fim ou recursão.
Defesas em camadas (esta é a 1ª): 1) análise da árvore sintática (aqui); 2) ambiente de execução com builtins
restritos e __import__ próprio (roteiro.py); 3) transação desfeita na simulação; 4) só em cópia liberada pela flag.
Compatível com IronPython 2.7/3.4 e CPython 3 (sem f-strings).
"""
import ast

try:
    TEXTO = basestring  # noqa: F821  (IronPython 2.7)
except NameError:
    TEXTO = str

MAX_CHARS = 20000
MAX_LINHAS = 400

# módulos que o roteiro pode importar (só estes; o executor repete a checagem em __import__)
MODULOS_OK = {
    "math", "re",
    "Autodesk.Revit.DB", "Autodesk.Revit.DB.Architecture", "Autodesk.Revit.DB.Structure",
    "Autodesk.Revit.DB.Mechanical", "Autodesk.Revit.DB.Plumbing", "Autodesk.Revit.DB.Electrical",
    "System.Collections.Generic",
}
# de System.Collections.Generic só coleções (nada de System.IO etc.)
NOMES_SYSTEM_OK = {"List", "IList", "Dictionary", "HashSet", "KeyValuePair"}

# nomes/atributos que nunca aparecem (por nome simples ou por .atributo)
PROIBIDOS = set("""
eval exec compile open file input raw_input __import__ globals locals vars dir getattr setattr delattr
reload execfile exit quit help breakpoint memoryview bytearray
clr os sys subprocess socket System Microsoft importlib ctypes pickle shutil pathlib io builtins __builtins__
threading multiprocessing asyncio signal atexit gc inspect types traceback
Transaction TransactionGroup SubTransaction TransactionStatus
GetType Assembly GetMethod GetMethods GetProperty GetProperties GetField GetFields Invoke InvokeMember
CreateInstance GetConstructor MakeGenericType DeclaringType Module GetModule Activator AppDomain Environment
Save SaveAs SaveAsLibrary Close Synchronize SynchronizeWithCentral OpenDocumentFile OpenAndActivateDocument
NewProjectDocument NewFamilyDocument NewProjectTemplateDocument
Export ExportImage ExportDWG ExportDXF ExportDGN ExportPDF ExportIFC ExportSAT ExportFBX ExportDWF ExportDWFX
Print PrintManager LoadFamily LoadFamilySymbol SaveToProjectAsImage
RevitLinkType CADLinkType ImageType ImportInstance ImageInstance
""".split())

# prefixos proibidos em atributos (cobre Export*, Print*, Import*, Load*)
PREFIXOS_PROIBIDOS = ("Export", "Print", "Import", "Load", "Save")

# nós sintáticos que não são permitidos
NOS_PROIBIDOS = tuple(getattr(ast, n) for n in (
    "While", "Global", "Nonlocal", "ClassDef", "AsyncFunctionDef", "AsyncFor", "AsyncWith", "Await",
    "Yield", "YieldFrom", "Exec",
) if hasattr(ast, n))


def _nome_proibido(nome):
    if nome in PROIBIDOS:
        return True
    if nome.startswith("__") or nome.endswith("__"):
        return True
    return False


def validar(codigo):
    """Devolve a lista de problemas (vazia = pode seguir para simulação). Cada item: 'linha N: motivo'."""
    if not isinstance(codigo, TEXTO):
        return ["o código deve ser texto"]
    if not codigo.strip():
        return ["código vazio"]
    if len(codigo) > MAX_CHARS:
        return ["código muito grande (máximo {} caracteres)".format(MAX_CHARS)]
    if codigo.count("\n") + 1 > MAX_LINHAS:
        return ["código com linhas demais (máximo {})".format(MAX_LINHAS)]
    if "\x00" in codigo:
        return ["caractere inválido no código"]
    try:
        arvore = ast.parse(codigo)
    except SyntaxError as e:
        return ["linha {}: erro de sintaxe ({})".format(getattr(e, "lineno", "?"), getattr(e, "msg", e))]
    except Exception as e:
        return ["não foi possível analisar o código ({})".format(e)]

    problemas = []

    def erro(no, motivo):
        problemas.append("linha {}: {}".format(getattr(no, "lineno", "?"), motivo))

    funcoes = {}  # nome -> nó, para achar recursão direta
    for no in ast.walk(arvore):
        if isinstance(no, NOS_PROIBIDOS):
            erro(no, "comando não permitido ({}); use 'for' sobre listas finitas".format(type(no).__name__.lower()))
        elif isinstance(no, ast.Import):
            for a in no.names:
                if a.name not in MODULOS_OK or a.name == "System.Collections.Generic":
                    erro(no, "import não permitido: {}".format(a.name))
        elif isinstance(no, ast.ImportFrom):
            mod = no.module or ""
            if no.level and no.level > 0:
                erro(no, "import relativo não permitido")
            elif mod not in MODULOS_OK:
                erro(no, "import não permitido: {}".format(mod))
            else:
                for a in no.names:
                    if a.name == "*":
                        erro(no, "import com * não permitido")
                    elif mod == "System.Collections.Generic" and a.name not in NOMES_SYSTEM_OK:
                        erro(no, "de System.Collections.Generic só é permitido: {}".format(", ".join(sorted(NOMES_SYSTEM_OK))))
                    elif _nome_proibido(a.name) or a.name.startswith(PREFIXOS_PROIBIDOS):
                        erro(no, "nome não permitido: {}".format(a.name))
        elif isinstance(no, ast.Name):
            if _nome_proibido(no.id):
                erro(no, "nome não permitido: {}".format(no.id))
        elif isinstance(no, ast.Attribute):
            if _nome_proibido(no.attr) or no.attr.startswith(PREFIXOS_PROIBIDOS):
                erro(no, "atributo não permitido: .{}".format(no.attr))
        elif isinstance(no, ast.FunctionDef):
            funcoes[no.name] = no
            if _nome_proibido(no.name):
                erro(no, "nome de função não permitido: {}".format(no.name))

    # recursão direta (função chamando a si mesma): causaria travamento
    for nome, f in funcoes.items():
        for no in ast.walk(f):
            if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id == nome:
                erro(f, "função recursiva não permitida: {}".format(nome))
                break
    # lista de problemas sem repetição, em ordem de linha
    vistos, saida = set(), []
    for p in problemas:
        if p not in vistos:
            vistos.add(p)
            saida.append(p)
    return saida[:20]
