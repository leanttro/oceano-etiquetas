import os
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, send_from_directory, render_template, make_response
from dotenv import load_dotenv
from flask_cors import CORS
import datetime
import traceback
import decimal

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
        
        # Busca pelo campo 'url_slug'
        
        # [CORREÇÃO 1]
        # REMOVIDA A LÓGICA DE MANIPULAÇÃO 'url_busca'.
        # A consulta agora usa 'slug' diretamente, como veio da URL.
        
        cur.execute('SELECT * FROM oceano_produtos WHERE url_slug = %s;', (slug,))
        produto = cur.fetchone()
        cur.close()

        if produto:
            # Formata os dados (datas, etc.) para o template
            produto_formatado = format_db_data(dict(produto))
            # Renderiza o template 'oceano-produto-detalhe.html'
            # e injeta os dados do banco na variável 'produto'
            return render_template('oceano-produto-detalhe.html', produto=produto_formatado)
        else:
            # [CORREÇÃO 1] Log atualizado para usar 'slug'
            print(f"AVISO: Produto com slug/url '{slug}' não encontrado.")
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
    """Serve o 'index.html' (que deve ser o seu 'teste.html' renomeado)"""
    return send_from_directory('.', 'index.html')

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
    app.run(host="0.0.0.0", port=port, debug=False)