"""Genera la página de QA a partir de los dos documentos del repositorio.

Se genera en vez de escribirse a mano para que no existan dos versiones del
mismo contenido que se desincronicen: la fuente son los .md del repo.
"""

import html
import io
import re
import sys

RUTA = "c:/Users/user/proyectos_vscode/inetum/plataforma_gestion/docs"


def leer(nombre):
    return io.open(f"{RUTA}/{nombre}", encoding="utf-8").read()


# --- markdown mínimo (el que usan estos documentos) ------------------------

def en_linea(texto):
    texto = html.escape(texto)
    texto = re.sub(r"`([^`]+)`", r"<code>\1</code>", texto)
    texto = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", texto)
    texto = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<em>\1</em>", texto)
    texto = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", texto)  # sin enlaces al repo
    return texto


def bloque_tabla(lineas):
    filas = [ln.strip().strip("|").split("|") for ln in lineas]
    cabecera, cuerpo = filas[0], filas[2:]
    out = ['<div class="scroll"><table><thead><tr>']
    out += [f"<th>{en_linea(c.strip())}</th>" for c in cabecera]
    out.append("</tr></thead><tbody>")
    for fila in cuerpo:
        out.append("<tr>" + "".join(f"<td>{en_linea(c.strip())}</td>" for c in fila) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def a_html(md):
    """Convierte el subconjunto de markdown que usan estos documentos."""
    salida, i = [], 0
    lineas = md.split("\n")

    while i < len(lineas):
        ln = lineas[i]

        if ln.startswith("```"):
            i += 1
            codigo = []
            while i < len(lineas) and not lineas[i].startswith("```"):
                codigo.append(html.escape(lineas[i]))
                i += 1
            salida.append("<pre>" + "\n".join(codigo) + "</pre>")

        elif ln.startswith("|"):
            tabla = []
            while i < len(lineas) and lineas[i].startswith("|"):
                tabla.append(lineas[i])
                i += 1
            salida.append(bloque_tabla(tabla))
            continue

        elif ln.startswith("### "):
            salida.append(f"<h3>{en_linea(ln[4:])}</h3>")
        elif ln.startswith("## "):
            salida.append(f"<h2>{en_linea(ln[3:])}</h2>")
        elif ln.startswith("# "):
            pass  # el título va en la cabecera de la página
        elif ln.startswith("> "):
            cita = [ln[2:]]
            while i + 1 < len(lineas) and lineas[i + 1].startswith(">"):
                i += 1
                cita.append(lineas[i].lstrip("> "))
            salida.append(f'<div class="nota">{en_linea(" ".join(cita))}</div>')
        elif ln.startswith("---"):
            salida.append("<hr>")
        elif re.match(r"^\d+\.\s", ln):
            items = []
            while i < len(lineas) and re.match(r"^\d+\.\s", lineas[i]):
                items.append(f"<li>{en_linea(re.sub(r'^\\d+\\.\\s', '', lineas[i]))}</li>")
                i += 1
            salida.append("<ol>" + "".join(items) + "</ol>")
            continue
        elif ln.startswith("- "):
            items = []
            while i < len(lineas) and lineas[i].startswith("- "):
                items.append(f"<li>{en_linea(lineas[i][2:])}</li>")
                i += 1
            salida.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif ln.strip():
            salida.append(f"<p>{en_linea(ln)}</p>")

        i += 1

    return "\n".join(salida)


# --- casos del plan --------------------------------------------------------

def extraer_casos(plan):
    bloques = []
    for m in re.finditer(r"^## \d+\.\s+([A-Z]+) — (.+?)$(.*?)(?=^## |\Z)", plan, re.S | re.M):
        sigla, titulo, cuerpo = m.group(1), m.group(2).strip(), m.group(3)
        casos = re.findall(r"^\| ([A-Z]+-[0-9]+[a-z]?) \| (.+?) \| (.+?) \| (.+?) \|$", cuerpo, re.M)
        if casos:
            intro = ""
            nota = re.search(r"^> (.+?)$", cuerpo, re.M)
            if nota:
                intro = f'<div class="nota">{en_linea(nota.group(1))}</div>'
            bloques.append((sigla, titulo, intro, casos))
    return bloques


def render_casos(bloques):
    out = []
    for sigla, titulo, intro, casos in bloques:
        out.append(f'<section class="bloque" id="b-{sigla}">')
        out.append(
            f'<div class="bloque-cab"><span class="sigla">{sigla}</span>'
            f"<h3>{en_linea(titulo)}</h3>"
            f'<span class="cuenta">{len(casos)} casos</span></div>'
        )
        out.append(intro)
        for cid, tit, pasos, esperado in casos:
            out.append(
                f'<label class="caso" for="c-{cid}">'
                f'<input type="checkbox" id="c-{cid}" data-caso="{cid}">'
                f'<div class="caso-cuerpo">'
                f'<div class="caso-cab"><span class="cid">{cid}</span>'
                f'<span class="ctit">{en_linea(tit)}</span></div>'
                f'<div class="campo"><span class="etq">Pasos</span>{en_linea(pasos)}</div>'
                f'<div class="campo"><span class="etq">Esperado</span>{en_linea(esperado)}</div>'
                f"</div></label>"
            )
        out.append("</section>")
    return "\n".join(out)


def main():
    manual = leer("QA_MANUAL.md")
    plan = leer("QA_PLAN_PRUEBAS.md")

    # Del plan solo interesan el entorno y los casos: el resto lo cubre el manual.
    entorno = re.search(r"## 1\. Entorno y cuentas(.*?)(?=^## 2\.)", plan, re.S | re.M).group(1)
    bloques = extraer_casos(plan)
    total = sum(len(c) for *_, c in bloques)

    indice = "".join(
        f'<a href="#b-{s}"><span>{s}</span><b>{len(c)}</b></a>' for s, _, _, c in bloques
    )

    plantilla = io.open(sys.argv[1], encoding="utf-8").read()
    pagina = (
        plantilla
        .replace("{{TOTAL}}", str(total))
        .replace("{{INDICE}}", indice)
        .replace("{{ENTORNO}}", a_html(entorno))
        .replace("{{MANUAL}}", a_html(manual))
        .replace("{{CASOS}}", render_casos(bloques))
    )
    io.open(sys.argv[2], "w", encoding="utf-8").write(pagina)
    print(f"generado: {total} casos en {len(bloques)} bloques")


if __name__ == "__main__":
    main()
