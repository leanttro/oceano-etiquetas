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
import collections # <-- NOVO: Importa collections para o menu ordenado

# Carrega variáveis de ambiente de um arquivo .env, se existir (para dev local)
# No Render, você vai setar as variáveis de ambiente na interface
load_dotenv()

# Inicializa o aplicativo Flask
# static_folder='.' -> Permite servir arquivos como 'index.html' e imagens da raiz.
# template_folder='templates' -> Onde o Flask vai procurar o 'oceano-produto-detalhe.html'.
app = Flask(__name__, static_folder='.', static_url_path='', template_folder='templates')
CORS(app) # Habilita CORS para todas as rotas

def get_db_connection():
    """Cria e retorna uma conexão com o banco de dados PostgreSQL."""
    conn = None
    try:
        # Pega a URL do banco de dados das variáveis de ambiente
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
    """
    Formata dados do banco (datas, decimais) para serem compatíveis com JSON.
    (Versão adaptada para 'oceano_produtos' que usa TIMESTAMPTZ)
    """
    if not isinstance(data_dict, dict):
        return data_dict

    formatted_dict = {}
    for key, value in data_dict.items():
        # Converte datetime.datetime e datetime.date para string ISO (seguro para JSON)
        if isinstance(value, (datetime.datetime, datetime.date)):
            formatted_dict[key] = value.isoformat() if value else None
        # Converte datetime.time para string
        elif isinstance(value, datetime.time):
            formatted_dict[key] = value.strftime('%H:%M') if value else None
        # Converte Decimal para float
        elif isinstance(value, decimal.Decimal):
            try:
                formatted_dict[key] = float(value)
            except (TypeError, ValueError):
                formatted_dict[key] = None
        else:
            formatted_dict[key] = value
    return formatted_dict


# --- NOVO: PROVEDOR DE CONTEXTO PARA O MENU DINÂMICO ---
@app.context_processor
def inject_dynamic_menu():
    """
    Injeta dados do menu em todos os templates renderizados.
    Consulta o BD e agrupa os produtos por categoria.
    """
    conn = None
    # Garante a ordem desejada das categorias
    categorias_ordem = ['Lacres', 'Adesivos', 'Brindes', 'Impressos']
    menu_data = collections.OrderedDict([(cat, []) for cat in categorias_ordem])

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # Busca apenas os campos necessários para o menu
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
            produto_data = {
                'nome': produto['nome_produto'],
                'url': produto['url_slug'] # O BD já salva o /produtos/slug
            }
            
            if cat in menu_data:
                menu_data[cat].append(produto_data)
            else: 
                # Adiciona categorias não previstas (ex: 'Outros') no final
                if cat not in menu_data:
                    menu_data[cat] = []
                menu_data[cat].append(produto_data)
        
        # Remove categorias que buscamos mas que não têm produtos
        menu_data_final = {k: v for k, v in menu_data.items() if v}
        
        return dict(menu_categorias=menu_data_final)

    except Exception as e:
        print(f"ERRO CRÍTICO ao gerar menu dinâmico: {e}")
        traceback.print_exc()
        # Retorna um dicionário vazio em caso de falha no BD
        return dict(menu_categorias=collections.OrderedDict())
    finally:
        if conn: conn.close()
# --- FIM DO PROVEDOR DE CONTEXTO ---


# --- ROTAS DA 'OCEANO ETIQUETAS' ---

@app.route('/api/produtos')
def get_api_produtos():
    """Retorna uma lista JSON de todos os produtos da tabela 'oceano_produtos'."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Filtro opcional por categoria (ex: /api/produtos?categoria=Lacres)
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

        # Processa os dados (formatação de datas, etc.)
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


# --- ROTA DE DETALHE ÚNICA PARA PRODUTOS ---
# Ex: /produtos/lacre-destrutivel-casca-ovo
@app.route('/produtos/<path:slug>') 
def produto_detalhe(slug):
    """Renderiza a página de detalhe de um produto buscando pelo 'url_slug'."""
    conn = None
    try: 
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # [CORREÇÃO 1 - APLICADA]
        # O 'slug' da URL é 'lacre-destrutivel-casca-ovo'
        # O banco de dados (via Colab) salva '/produtos/lacre-destrutivel-casca-ovo'
        # A consulta DEVE buscar pelo caminho completo.
        url_busca = f"/produtos/{slug}"
        
        cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (url_busca,))
        produto = cur.fetchone()
        cur.close()

        if produto:
            # Formata os dados (datas, etc.) para o template
            produto_formatado = format_db_data(dict(produto))
            
            # --- [INÍCIO DA CORREÇÃO 4] ---
            # O template espera um objeto 'specs'. O banco de dados fornece
            # uma string 'especificacoes_tecnicas'. Precisamos fazer o parse.
            specs_json_string = produto_formatado.get('especificacoes_tecnicas')
            specs_dict = {} # Começa com um dicionário vazio por segurança
            
            if specs_json_string:
                try:
                    # Tenta fazer o parse da string JSON
                    specs_dict = json.loads(specs_json_string)
                except json.JSONDecodeError:
                    # Se falhar (ex: texto simples), loga o aviso e deixa specs_dict vazio
                    print(f"AVISO: Falha ao decodificar JSON de especificacoes_tecnicas para o slug '{slug}'.")
            
            # Adiciona o dict 'specs' ao 'produto_formatado' que vai para o template.
            # Se o parse falhou ou a string era vazia, 'specs' será {}
            produto_formatado['specs'] = specs_dict
            # --- [FIM DA CORREÇÃO 4] ---
            
            # Renderiza o template 'oceano-produto-detalhe.html'
            # e injeta os dados do banco na variável 'produto'
            return render_template('oceano-produto-detalhe.html', produto=produto_formatado)
        else:
            # [CORREÇÃO 1] Log atualizado para usar 'url_busca'
            print(f"AVISO: Produto com slug/url '{url_busca}' não encontrado.")
            return "Produto não encontrado", 404
            
    except Exception as e:
        print(f"ERRO na rota /produtos/{slug}: {e}")
        traceback.print_exc()
        return "Erro ao carregar a página do produto", 500
    finally:
        if conn: conn.close()


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
    """
    Serve arquivos estáticos (CSS, JS, imagens, etc.) ou retorna 404
    (Lógica copiada do seu app.py de feiras)
    """
    basename = os.path.basename(path)
    
    # 1. Se não contém um ponto, NÃO é um arquivo estático (ex: /produtos/slug-do-produto)
    #    Isso deve ter sido pego pela rota @app.route('/produtos/<path:slug>')
    #    Se chegou aqui, é um slug que não foi encontrado.
    if '.' not in basename:
        print(f"AVISO: Tentativa de acesso a um slug não encontrado (sem ponto no basename): {path}")
        return "Not Found", 404
        
    # 2. Se contém um ponto, TENTA servir como arquivo estático (.html, .css, .png, etc.)
    #    (O 'index.html' não é mais pego aqui, pois foi movido para 'templates/')
    if os.path.exists(os.path.join('.', path)):
        return send_from_directory('.', path)
    else:
        print(f"AVISO: Arquivo estático não encontrado: {path}")
        return "Not Found", 404

# --- Execução do App ---
if __name__ == '__main__':
    # O Render usa a variável 'PORT'
    port = int(os.environ.get("PORT", 10000))
    # debug=False é o padrão para produção
    # debug=True é útil para desenvolvimento local, pois recarrega
    # automaticamente e mostra erros detalhados no navegador.
    # Mude para True se estiver testando localmente.
    app.run(host="0.0.0.0", port=port, debug=False)