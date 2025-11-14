import os
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, send_from_directory, render_template, make_response
from dotenv import load_dotenv
from flask_cors import CORS
import datetime
import traceback
import decimal
import json 
import collections # Importa collections para o menu ordenado

# Carrega variáveis de ambiente
load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='', template_folder='templates')
CORS(app) 

def get_db_connection():
    """Cria e retorna uma conexão com o banco de dados PostgreSQL."""
    conn = None
    try:
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            print("ERRO CRÍTICO: Variável de ambiente DATABASE_URL não encontrada.")
            raise ValueError("DATABASE_URL não configurada")
            
        conn = psycopg2.connect(db_url)
        return conn
    except Exception as e:
        print(f"ERRO CRÍTICO: Não foi possível conectar ao banco de dados: {e}")
        raise

def format_db_data(data_dict):
    """Formata dados do banco (datas, decimais) para serem compatíveis com JSON."""
    if not isinstance(data_dict, dict):
        return data_dict

    formatted_dict = {}
    for key, value in data_dict.items():
        if isinstance(value, (datetime.datetime, datetime.date)):
            formatted_dict[key] = value.isoformat() if value else None
        elif isinstance(value, datetime.time):
            formatted_dict[key] = value.strftime('%H:%M') if value else None
        elif isinstance(value, decimal.Decimal):
            try:
                formatted_dict[key] = float(value)
            except (TypeError, ValueError):
                formatted_dict[key] = None
        else:
            formatted_dict[key] = value
    return formatted_dict


# --- [FUNÇÃO 1 - ATUALIZADA] ---
# Injeta o menu dinâmico em todos os templates
@app.context_processor
def inject_dynamic_menu():
    """
    Injeta dados do menu em todos os templates renderizados.
    Consulta o BD e agrupa os produtos por categoria.
    """
    conn = None
    categorias_ordem = ['Lacres', 'Adesivos', 'Brindes', 'Impressos']
    menu_data = collections.OrderedDict([(cat, []) for cat in categorias_ordem])

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        query = """
            SELECT nome_produto, url_slug, categoria 
            FROM oceano_produtos 
            WHERE categoria IS NOT NULL AND categoria != '' AND url_slug IS NOT NULL AND url_slug != ''
            ORDER BY categoria, nome_produto;
        """
        cur.execute(query)
        produtos = cur.fetchall()
        cur.close()

        # Agrupa os produtos
        for produto in produtos:
            cat = produto['categoria']
            
            # --- [LÓGICA DE LINK ROBUSTO] ---
            slug_do_bd = produto['url_slug']
            
            # 1. Remove o prefixo '/produtos/' se ele existir, para termos o slug limpo
            if slug_do_bd.startswith('/produtos/'):
                slug_limpo = slug_do_bd[len('/produtos/'):]
            else:
                slug_limpo = slug_do_bd # Ex: 'adesivos/TESTE'
            
            # 2. Monta a URL final que o usuário vai clicar
            # Isso garante que o link no HTML será sempre /produtos/slug-limpo
            url_final_para_link = f"/produtos/{slug_limpo}"

            produto_data = {
                'nome': produto['nome_produto'],
                'url': url_final_para_link 
            }
            # --- [FIM DA LÓGICA DE LINK] ---
            
            if cat in menu_data:
                menu_data[cat].append(produto_data)
            elif cat not in menu_data: 
                menu_data[cat] = [produto_data]
        
        menu_data_final = {k: v for k, v in menu_data.items() if v}
        
        return dict(menu_categorias=menu_data_final)

    except Exception as e:
        print(f"ERRO CRÍTICO ao gerar menu dinâmico: {e}")
        traceback.print_exc()
        return dict(menu_categorias=collections.OrderedDict())
    finally:
        if conn: conn.close()
# --- FIM DA FUNÇÃO 1 ---


# --- ROTAS DA 'OCEANO ETIQUETAS' ---

@app.route('/api/produtos')
def get_api_produtos():
    """Retorna uma lista JSON de todos os produtos da tabela 'oceano_produtos'."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        categoria_filtro = request.args.get('categoria')
        
        query = "SELECT * FROM oceano_produtos"
        params = []

        if categoria_filtro:
            query += " WHERE categoria ILIKE %s"
            params.append(f"%{categoria_filtro}%")

        query += " ORDER BY codigo_produto;"
        
        cur.execute(query, tuple(params))
        produtos_raw = cur.fetchall()
        cur.close()

        produtos_processados = [format_db_data(dict(produto)) for produto in produtos_raw]
        return jsonify(produtos_processados)
        
    except psycopg2.errors.UndefinedTable:
        print("ERRO: A tabela 'oceano_produtos' não foi encontrada no banco de dados.")
        return jsonify({'error': 'Tabela oceano_produtos não encontrada.'}), 500
    except Exception as e:
        print(f"ERRO no endpoint /api/produtos: {e}")
        traceback.print_exc()
        return jsonify({'error': 'Erro interno ao buscar produtos.'}), 500
    finally:
        if conn: conn.close()


# --- [FUNÇÃO 2 - ATUALIZADA] ---
# Rota de detalhe única para produtos
@app.route('/produtos/<path:slug>') 
def produto_detalhe(slug):
    """Renderiza a página de detalhe de um produto buscando pelo 'url_slug'."""
    conn = None
    try: 
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # --- [INÍCIO DA LÓGICA DE BUSCA ROBUSTA] ---
        # O 'slug' da URL é, por exemplo, 'adesivos/TESTE'
        # O BD pode ter '/produtos/adesivos/TESTE' (do script de 'inserir')
        # OU 'adesivos/TESTE' (do script de 'editar', como visto no screenshot)
        
        # Tentativa 1: Buscar pelo slug com prefixo (o formato correto/novo)
        url_busca_com_prefixo = f"/produtos/{slug}"
        cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (url_busca_com_prefixo,))
        produto = cur.fetchone()

        if not produto:
            # Tentativa 2: Buscar pelo slug exato (formato legado/editado)
            print(f"AVISO: Produto com slug/url '{url_busca_com_prefixo}' não encontrado. Tentando busca legada por '{slug}'.")
            cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (slug,))
            produto = cur.fetchone()
        
        # --- [FIM DA LÓGICA DE BUSCA ROBUSTA] ---

        cur.close()

        if produto:
            # Formata os dados (datas, etc.) para o template
            produto_formatado = format_db_data(dict(produto))
            
            # Lógica para converter 'especificacoes_tecnicas' (string JSON) em um dict 'specs'
            specs_json_string = produto_formatado.get('especificacoes_tecnicas')
            specs_dict = {} 
            
            if specs_json_string:
                try:
                    specs_dict = json.loads(specs_json_string)
                except json.JSONDecodeError:
                    print(f"AVISO: Falha ao decodificar JSON de especificacoes_tecnicas para o slug '{slug}'.")
            
            produto_formatado['specs'] = specs_dict
            
            # Renderiza o template e injeta os dados na variável 'produto'
            return render_template('oceano-produto-detalhe.html', produto=produto_formatado)
        else:
            # Se ambas as tentativas falharem
            print(f"ERRO FINAL: Produto não encontrado para '{url_busca_com_prefixo}' ou '{slug}'.")
            return "Produto não encontrado", 404
            
    except Exception as e:
        print(f"ERRO na rota /produtos/{slug}: {e}")
        traceback.print_exc()
        return "Erro ao carregar a página do produto", 500
    finally:
        if conn: conn.close()
# --- FIM DA FUNÇÃO 2 ---


# --- ROTAS PARA SERVIR ARQUIVOS (DEVE VIR POR ÚLTIMO) ---

@app.route('/')
def index_route():
    """
    [ALTERADO] Renderiza o 'index.html' dinamicamente usando Jinja2.
    O arquivo 'index.html' DEVE estar na pasta 'templates/'.
    """
    return render_template('index.html')

@app.route('/<path:path>')
def serve_static_files(path):
    """Serve arquivos estáticos (CSS, JS, imagens, etc.) ou retorna 404"""
    basename = os.path.basename(path)
    
    if '.' not in basename:
        print(f"AVISO: Tentativa de acesso a um slug não encontrado (sem ponto no basename): {path}")
        return "Not Found", 404
        
    if os.path.exists(os.path.join('.', path)):
        return send_from_directory('.', path)
    else:
        print(f"AVISO: Arquivo estático não encontrado: {path}")
        return "Not Found", 404

# --- Execução do App ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    # Mude debug=True para desenvolvimento local
    app.run(host="0.0.0.0", port=port, debug=False)